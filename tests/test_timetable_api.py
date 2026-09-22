"""Role boundaries and full draft/publication lifecycle on an isolated DB."""
import json
import datetime as dt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from backend.app.auth import create_token
from backend.app.models import Notification
from backend.app.database import Base, get_session
from backend.app.main import app
from backend.app.timetable import api as timetable_api, models as m, operations, service as s, reads
from backend.app.timetable.solver import solve
from timetable_fixtures import college


@pytest.fixture()
def setup(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    data = college(db)
    def sessions():
        with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    # Solver process behavior is covered separately; HTTP lifecycle exercises pure solve here.
    monkeypatch.setattr(timetable_api, 'run_solver', lambda data, **kw: (solve(data, **kw), 1.0))
    monkeypatch.setattr(operations, 'run_solver', lambda data, **kw: (solve(data, **kw), 1.0))
    data.update(db=db, engine=engine, factory=factory, client=TestClient(app))
    yield data
    app.dependency_overrides.pop(get_session, None)
    db.close()
    engine.dispose()


def headers(data, role):
    return {'Authorization': f"Bearer {create_token(data['users'][role])}"}


def request(data, method, path, role='hod', **kwargs):
    return getattr(data['client'], method)(path, headers=headers(data, role), **kwargs)


def generate(data, **body):
    r = request(data, 'post', f"/api/hod/timetable/terms/{data['term'].id}/runs", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def publish(data, run, role='hod'):
    body = {'action': 'publish_timetable_draft', 'run_id': run['id']}
    if role in {'admin', 'principal'}:
        body['department'] = run['dept_code']
    preview = request(data, 'post', '/api/timetable/operations/preview', role, json=body)
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    confirmed = request(data, 'post', '/api/timetable/operations/confirm', role, json={
        'preview_id': payload['preview_id'], 'confirmation_token': payload['confirmation_token']})
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def test_complete_draft_is_not_published_until_explicit_action(setup):
    d = setup
    readiness = request(d, 'post', f"/api/hod/timetable/terms/{d['term'].id}/preflight")
    assert readiness.status_code == 200 and readiness.json()['ready']
    run = generate(d)
    assert run['status'] == 'COMPLETE' and not run['conflicts']
    assert request(d, 'get', '/api/student/timetable', 'student').json()['published'] is False
    response = publish(d, run)
    assert response['result']['status'] == 'PUBLISHED'
    notices = d['db'].query(Notification).filter_by(notification_type='TIMETABLE_PUBLISHED').all()
    assert {notice.recipient_user_id for notice in notices} == {
        d['users']['student'].id, d['users']['faculty'].id}
    weekly = request(d, 'get', '/api/student/timetable', 'student').json()
    assert weekly['published'] and len(weekly['weekly']) == 4
    assert weekly['weekly'][0]['faculty'] == 'Qualified Teacher'
    assert d['db'].query(m.Audit).filter_by(run_id=run['id']).count() == 3
    assert d['db'].query(m.OperationEvent).filter_by(
        action='publish_timetable_draft', phase='CONFIRMED').count() == 1
    audit_response = request(d, 'get', '/api/timetable/operations/audit').json()
    confirmed_audit = next(row for row in audit_response if row['phase'] == 'CONFIRMED')
    assert confirmed_audit['actor_id'] == d['users']['hod'].id
    assert confirmed_audit['requested_action']['action'] == 'publish_timetable_draft'
    assert confirmed_audit['affected_records'] == [{'type': 'timetable_run', 'id': run['id']}]
    audit_event = d['db'].query(m.OperationEvent).filter_by(phase='CONFIRMED').one()
    audit_event.action = 'tampered'
    with pytest.raises(ValueError, match='append-only'):
        d['db'].commit()
    d['db'].rollback()


def test_legacy_publish_route_cannot_bypass_preview_confirmation(setup):
    run = generate(setup)
    response = request(setup, 'post', f"/api/hod/timetable/runs/{run['id']}/publish")
    assert response.status_code == 409 and 'preview' in response.text.lower()
    assert setup['db'].get(m.Run, run['id']).status == 'COMPLETE'


def test_admin_can_preview_and_confirm_department_draft_generation(setup):
    d = setup
    published = generate(d, seed=13)
    publish(d, published)
    before_count = d['db'].query(m.Run).count()
    preview = request(d, 'post', '/api/timetable/operations/preview', 'admin', json={
        'action': 'generate_timetable_draft', 'term_id': d['term'].id,
        'department': d['dept'], 'seed': 31})
    assert preview.status_code == 200 and preview.json()['summary']['ready']
    assert d['db'].query(m.Run).count() == before_count
    confirmed = request(d, 'post', '/api/timetable/operations/confirm', 'admin', json={
        'preview_id': preview.json()['preview_id'],
        'confirmation_token': preview.json()['confirmation_token']})
    assert confirmed.status_code == 200
    result = confirmed.json()['result']
    assert result['status'] == 'DRAFT'
    draft = d['db'].get(m.Run, result['draft_run_id'])
    assert draft.status == 'DRAFT'
    assert d['db'].query(m.Entry).filter_by(run_id=draft.id).count() > 0
    assert d['db'].query(m.Run).count() == before_count + 1
    assert d['db'].get(m.Run, published['id']).status == 'PUBLISHED'
    duplicate = request(d, 'post', '/api/timetable/operations/confirm', 'admin', json={
        'preview_id': preview.json()['preview_id'],
        'confirmation_token': preview.json()['confirmation_token']})
    assert duplicate.status_code == 409
    assert d['db'].query(m.Run).count() == before_count + 1
    assert d['db'].query(m.OperationEvent).filter_by(
        phase='CONFIRMED', action='generate_timetable_draft').count() == 1


def test_admin_validates_and_publishes_confirmed_draft_without_changing_current_version_early(setup):
    d = setup
    old = generate(d, seed=13); publish(d, old)
    generation = request(d, 'post', '/api/timetable/operations/preview', 'admin', json={
        'action': 'generate_timetable_draft', 'term_id': d['term'].id, 'department': d['dept']}).json()
    created = request(d, 'post', '/api/timetable/operations/confirm', 'admin', json={
        'preview_id': generation['preview_id'], 'confirmation_token': generation['confirmation_token']}).json()
    draft_id = created['result']['draft_run_id']
    assert d['db'].get(m.Run, old['id']).status == 'PUBLISHED'
    validation = request(d, 'post', '/api/timetable/operations/preview', 'admin', json={
        'action': 'validate_timetable_draft', 'department': d['dept'], 'run_id': draft_id})
    assert validation.status_code == 200
    validated = request(d, 'post', '/api/timetable/operations/confirm', 'admin', json={
        'preview_id': validation.json()['preview_id'], 'confirmation_token': validation.json()['confirmation_token']})
    assert validated.status_code == 200 and validated.json()['result']['status'] == 'COMPLETE'
    publication = request(d, 'post', '/api/timetable/operations/preview', 'admin', json={
        'action': 'publish_timetable_draft', 'department': d['dept'], 'run_id': draft_id})
    assert publication.status_code == 200
    confirmed = request(d, 'post', '/api/timetable/operations/confirm', 'admin', json={
        'preview_id': publication.json()['preview_id'], 'confirmation_token': publication.json()['confirmation_token']})
    assert confirmed.status_code == 200
    assert d['db'].get(m.Run, draft_id).status == 'PUBLISHED'
    assert d['db'].get(m.Run, old['id']).status == 'ARCHIVED'


def test_generation_and_confirmation_audit_commit_atomically(setup, monkeypatch):
    d = setup
    preview = request(d, 'post', '/api/timetable/operations/preview', 'admin', json={
        'action': 'generate_timetable_draft', 'term_id': d['term'].id,
        'department': d['dept']}).json()
    original = operations._event

    def fail_confirmation(row, user, phase, affected, before, after=None):
        if phase == 'CONFIRMED':
            raise RuntimeError('injected audit failure')
        return original(row, user, phase, affected, before, after)

    monkeypatch.setattr(operations, '_event', fail_confirmation)
    response = request(d, 'post', '/api/timetable/operations/confirm', 'admin', json={
        'preview_id': preview['preview_id'],
        'confirmation_token': preview['confirmation_token']})
    assert response.status_code == 500
    assert d['db'].query(m.Run).count() == 0
    assert d['db'].query(m.OperationEvent).filter_by(phase='CONFIRMED').count() == 0


def test_operation_schema_rejects_identity_overrides_and_cross_department_scope(setup):
    d = setup
    forged = request(d, 'post', '/api/timetable/operations/preview', json={
        'action': 'generate_timetable_draft', 'term_id': d['term'].id,
        'actor_id': d['users']['admin'].id})
    assert forged.status_code == 422
    foreign = request(d, 'post', '/api/timetable/operations/preview', 'other_hod', json={
        'action': 'get_timetable_conflicts', 'run_id': generate(d)['id']})
    assert foreign.status_code == 404


def test_faculty_reschedule_request_requires_hod_confirmation_and_updates_safe_view(setup):
    d = setup
    run = generate(d); publish(d, run)
    entry = run['entries'][0]
    source = dt.date.today()
    while source.weekday() != entry['day']:
        source += dt.timedelta(days=1)
    proposed = request(d, 'post', '/api/timetable/operations/preview', 'faculty', json={
        'action': 'preview_replacement_slot', 'entry_id': entry['id'],
        'occurrence_date': source.isoformat()})
    assert proposed.status_code == 200, proposed.text
    assert d['db'].query(m.OccurrenceChange).count() == 0
    pending = request(d, 'get', '/api/timetable/operations/pending').json()
    assert proposed.json()['preview_id'] in {item['preview_id'] for item in pending}
    confirmed = request(d, 'post', '/api/timetable/operations/confirm', json={
        'preview_id': proposed.json()['preview_id']})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['result']['status'] == 'RESCHEDULED'
    student = request(d, 'get', '/api/student/timetable', 'student').json()
    assert student['changes'][0]['status'] == 'RESCHEDULED'
    assert d['db'].query(m.OperationEvent).filter_by(phase='CONFIRMED').count() >= 2


def test_cancel_preview_rechecks_and_prevents_duplicate_occurrence_changes(setup):
    d = setup
    run = generate(d); publish(d, run)
    entry = run['entries'][0]
    source = dt.date.today()
    while source.weekday() != entry['day']:
        source += dt.timedelta(days=1)
    body = {'action': 'preview_cancel_class', 'entry_id': entry['id'],
            'occurrence_date': source.isoformat()}
    first = request(d, 'post', '/api/timetable/operations/preview', json=body).json()
    assert request(d, 'post', '/api/timetable/operations/confirm', json={
        'preview_id': first['preview_id'], 'confirmation_token': first['confirmation_token']}).status_code == 200
    assert request(d, 'post', '/api/timetable/operations/preview', json=body).status_code == 409


@pytest.mark.parametrize('role', ['student', 'faculty', 'admin', 'principal'])
@pytest.mark.parametrize('action', ['preflight', 'runs'])
def test_only_hod_can_generate_or_preflight(setup, role, action):
    response = request(setup, 'post', f"/api/hod/timetable/terms/{setup['term'].id}/{action}", role, json={})
    assert response.status_code == 403


@pytest.mark.parametrize('role', ['student', 'faculty', 'admin', 'principal', 'other_hod'])
@pytest.mark.parametrize('action', ['publish', 'validate'])
def test_draft_mutations_enforce_role_and_department(setup, role, action):
    run = generate(setup)
    response = request(setup, 'post', f"/api/hod/timetable/runs/{run['id']}/{action}", role)
    assert response.status_code == (404 if role == 'other_hod' else 403)


@pytest.mark.parametrize('role,expected', [('student',403), ('faculty',403), ('other_hod',404), ('principal',200), ('admin',200), ('hod',200)])
def test_run_read_access(setup, role, expected):
    run = generate(setup)
    assert request(setup, 'get', f"/api/timetable/runs/{run['id']}", role).status_code == expected


def test_cross_department_section_assignment_faculty_room_ids_are_rejected(setup):
    d = setup
    prefix = f"/api/hod/timetable/terms/{d['term'].id}"
    req = {'section_id': d['foreign_section'].id, 'assignment_id': d['assignment'].id, 'periods_per_week': 4, 'max_per_day': 1}
    assert request(d, 'post', prefix+'/requirements', json=req).status_code == 404
    assert request(d, 'put', prefix+'/faculty-limits', json={'faculty_id': d['outside'].id, 'daily_limit': 4, 'weekly_limit': 20}).status_code == 404
    assert request(d, 'post', '/api/hod/timetable/qualifications', json={'faculty_id': d['outside'].id, 'subject_code': d['subject'].code}).status_code == 404
    assert request(d, 'put', f"/api/timetable/terms/{d['term'].id}/rooms/{d['foreign_room'].id}/availability", json={'unavailable_period_ids': []}).status_code == 404
    assert request(d, 'get', f"/api/timetable/{d['other']}/3/A").status_code == 404
    assert request(d, 'get', f"/api/timetable/{d['other']}/3/A/csv", 'student').status_code == 404


def test_bootstrap_preview_authorization_and_forged_department(setup, monkeypatch):
    d = setup
    path = f"/api/timetable/terms/{d['term'].id}/bootstrap"
    before = {model: d['db'].query(model).count() for model in
              (m.Section, m.Requirement, m.Qualification, m.Run, m.Entry)}
    preview = request(d, 'post', path, json={'use_subject_credits': True})
    assert preview.status_code == 200
    assert preview.json()['mode'] == 'dry-run'
    assert {x['code'] for x in preview.json()['departments']} == {d['dept']}
    for role in ('student', 'faculty', 'principal'):
        assert request(d, 'post', path, role, json={}).status_code == 403
    assert request(d, 'post', path, 'other_hod', json={'department': d['dept']}).status_code == 404
    admin = request(d, 'post', path, 'admin', json={'department': d['dept']})
    assert admin.status_code == 200 and {x['code'] for x in admin.json()['departments']} == {d['dept']}
    monkeypatch.delenv('MAWOS_ALLOW_TIMETABLE_BOOTSTRAP', raising=False)
    denied = request(d, 'post', path, json={'apply': True, 'confirm_apply': True,
                                            'preview_hash': preview.json()['preview_hash'],
                                            'use_subject_credits': True})
    assert denied.status_code == 422 and 'MAWOS_ALLOW_TIMETABLE_BOOTSTRAP' in denied.text
    d['db'].expire_all()
    assert before == {model: d['db'].query(model).count() for model in before}


def test_forged_student_and_faculty_identity_is_never_used(setup):
    d = setup
    run = generate(d)
    publish(d, run)
    for role in ('student', 'faculty'):
        own = request(d, 'get', f'/api/{role}/timetable', role).json()
        forged = request(d, 'get', f'/api/{role}/timetable?faculty_id={d["outside"].id}&usn=forged&section=B', role).json()
        assert own == forged
    assert request(d, 'put', f"/api/faculty/timetable/terms/{d['term'].id}/availability", 'faculty', json={'faculty_id': d['outside'].id, 'unavailable_period_ids': []}).status_code == 422


def test_lock_and_regenerate_retains_fixed_entry_and_parent(setup):
    d = setup
    run = generate(d)
    first = run['entries'][0]
    locked = request(d, 'patch', f"/api/hod/timetable/runs/{run['id']}/entries/{first['id']}/lock", json={'locked': True})
    assert locked.status_code == 200 and locked.json()['entries'][0]['locked']
    new = generate(d, seed=99, parent_run_id=run['id'])
    expected = {k: v for k, v in first.items() if k not in ('id', 'locked')}
    assert any({k:v for k,v in e.items() if k not in ('id','locked')} == expected and e['locked'] for e in new['entries'])
    assert new['parent_run_id'] == run['id']


def test_partial_run_cannot_publish_and_reports_missing_requirements(setup):
    d = setup
    run = generate(d, max_steps=100)
    assert run['status'] == 'PARTIAL' and run['unplaced']
    response = request(d, 'post', '/api/timetable/operations/preview', json={
        'action': 'publish_timetable_draft', 'run_id': run['id']})
    assert response.status_code == 409
    assert not request(d, 'get', '/api/student/timetable', 'student').json()['published']


def test_config_change_prevents_stale_publication(setup):
    d = setup
    run = generate(d)
    d['requirement'].periods_per_week = 5
    d['db'].commit()
    assert request(d, 'post', '/api/timetable/operations/preview', json={
        'action': 'publish_timetable_draft', 'run_id': run['id']}).status_code == 409
    checked = request(d, 'post', f"/api/hod/timetable/runs/{run['id']}/validate").json()
    assert 'stale_configuration' in {i['code'] for i in checked['conflicts']}


def test_second_publication_archives_first_and_history_is_immutable(setup):
    d = setup
    first, second = generate(d), generate(d, seed=22)
    for r in (first, second):
        publish(d, r)
    history = request(d, 'get', '/api/timetable/runs').json()
    assert {r['id']: r['status'] for r in history} == {first['id']: 'ARCHIVED', second['id']: 'PUBLISHED'}
    for run in (first, second):
        assert request(d, 'patch', f"/api/hod/timetable/runs/{run['id']}/entries/{run['entries'][0]['id']}/lock", json={'locked': True}).status_code == 409
    assert request(d, 'put', f"/api/faculty/timetable/terms/{d['term'].id}/availability", 'faculty', json={'unavailable_period_ids': []}).status_code == 409


def test_every_personal_get_is_read_only_even_when_published(setup, monkeypatch):
    d = setup
    run = generate(d)
    publish(d, run)
    def forbidden(*a, **kw):
        raise AssertionError('GET attempted a write')
    monkeypatch.setattr(Session, 'commit', forbidden)
    monkeypatch.setattr(Session, 'flush', forbidden)
    for role in ('student', 'faculty'):
        for suffix in ('', '/weekly', '/today', '/current-next'):
            result = request(d, 'get', f'/api/{role}/timetable{suffix}', role)
            assert result.status_code == 200
    assert request(d, 'get', f"/api/timetable/{d['dept']}/3/A", 'student').status_code == 200
    assert request(d, 'get', f"/api/timetable/{d['dept']}/3/A/csv", 'student').status_code == 200


def test_missing_configuration_returns_actionable_controlled_error(setup):
    d = setup
    d['db'].query(m.Qualification).delete()
    d['db'].commit()
    response = request(d, 'post', f"/api/hod/timetable/terms/{d['term'].id}/runs", json={})
    assert response.status_code == 422
    assert 'qualification' in {i['code'] for i in response.json()['detail']['issues']}
    assert d['db'].query(m.Run).count() == 0


def test_generation_and_publication_failures_rollback_every_row(setup, monkeypatch):
    d = setup
    original = s.audit
    def fail(*a, **kw):
        raise RuntimeError('SECRET database internals')
    monkeypatch.setattr(s, 'audit', fail)
    response = request(d, 'post', f"/api/hod/timetable/terms/{d['term'].id}/runs", json={})
    assert response.status_code == 500 and 'SECRET' not in response.text
    assert d['db'].query(m.Run).count() == d['db'].query(m.Entry).count() == 0
    monkeypatch.setattr(s, 'audit', original)
    first, second = generate(d), generate(d, seed=8)
    publish(d, first)
    def fail_publish(db, user, event, **kw):
        if event == 'timetable.published':
            raise RuntimeError('SECRET publication failure')
        return original(db, user, event, **kw)
    monkeypatch.setattr(s, 'audit', fail_publish)
    preview = request(d, 'post', '/api/timetable/operations/preview', json={
        'action': 'publish_timetable_draft', 'run_id': second['id']}).json()
    response = request(d, 'post', '/api/timetable/operations/confirm', json={
        'preview_id': preview['preview_id'], 'confirmation_token': preview['confirmation_token']})
    assert response.status_code == 500 and 'SECRET' not in response.text
    d['db'].expire_all()
    assert d['db'].get(m.Run, first['id']).status == 'PUBLISHED'
    assert d['db'].get(m.Run, second['id']).status == 'COMPLETE'
    assert d['db'].query(m.Audit).filter_by(event='timetable.archived').count() == 0


def test_config_race_or_lock_race_rejects_entire_generation(setup, monkeypatch):
    d = setup
    def race(data, **kw):
        with d['factory']() as session:
            session.get(m.Requirement, d['requirement'].id).periods_per_week = 5
            session.commit()
        return solve(data, **kw), 1
    monkeypatch.setattr(timetable_api, 'run_solver', race)
    response = request(d, 'post', f"/api/hod/timetable/terms/{d['term'].id}/runs", json={})
    assert response.status_code == 409
    assert d['db'].query(m.Run).count() == 0


@pytest.mark.parametrize('role', ['student', 'faculty', 'hod', 'principal'])
def test_admin_configuration_is_role_restricted(setup, role):
    assert request(setup, 'get', '/api/admin/timetable/configuration', role).status_code == 403
    assert request(setup, 'post', '/api/admin/timetable/rooms', role, json={'name': 'x', 'dept_code': setup['dept'], 'kind': 'lab', 'capacity': 30}).status_code == 403


def test_admin_period_configuration_and_own_faculty_availability(setup):
    d = setup
    url = f"/api/admin/timetable/terms/{d['term'].id}/periods"
    overlap = {'periods': [{'day_of_week': 0, 'period_index': 0, 'starts_at': '09:00', 'ends_at': '11:00'}, {'day_of_week': 0, 'period_index': 1, 'starts_at': '10:00', 'ends_at': '12:00'}]}
    assert request(d, 'put', url, 'admin', json=overlap).status_code == 422
    periods = request(d, 'get', f"/api/timetable/terms/{d['term'].id}/periods", 'faculty').json()['periods']
    ids = [periods[0]['id']]
    response = request(d, 'put', f"/api/faculty/timetable/terms/{d['term'].id}/availability", 'faculty', json={'unavailable_period_ids': ids})
    assert response.status_code == 200 and response.json()['unavailable_period_ids'] == ids
    assert request(d, 'put', f"/api/faculty/timetable/terms/{d['term'].id}/availability", 'faculty', json={'unavailable_period_ids': [99999]}).status_code == 404


def test_legacy_generation_is_retired_and_principal_overview_is_read_only(setup):
    assert request(setup, 'post', '/api/hod/generate-timetable').status_code == 410
    response = request(setup, 'get', '/api/principal/timetable/overview', 'principal')
    assert response.status_code == 200
    assert all(r['published_run_id'] is None for r in response.json())


@pytest.mark.parametrize('role', ['student', 'faculty', 'admin', 'principal', 'other_hod'])
def test_lock_authorization_for_every_role(setup, role):
    run = generate(setup)
    result = request(setup, 'patch', f"/api/hod/timetable/runs/{run['id']}/entries/{run['entries'][0]['id']}/lock", role, json={'locked': True})
    assert result.status_code == (404 if role == 'other_hod' else 403)


def test_invalid_cross_department_source_run_and_forged_entry_are_rejected(setup):
    run = generate(setup)
    response = request(setup, 'post', f"/api/hod/timetable/terms/{setup['term'].id}/runs", 'other_hod', json={'parent_run_id': run['id']})
    assert response.status_code == 404
    assert request(setup, 'patch', f"/api/hod/timetable/runs/{run['id']}/entries/99999/lock", json={'locked': True}).status_code == 404


def test_startup_never_generates_timetable_data(setup, monkeypatch):
    from backend.app import main
    def forbidden(*a, **kw): raise AssertionError('Unexpected automatic timetable generation')
    class Agent:
        generate = forbidden
    monkeypatch.setattr(main, 'engine', setup['engine'])
    monkeypatch.setattr(main, 'get_agents', lambda: {'timetable_agent': Agent()})
    monkeypatch.setattr(main.config, 'seed_demo_data_enabled', lambda: False)
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
    assert setup['db'].query(m.Run).count() == 0
