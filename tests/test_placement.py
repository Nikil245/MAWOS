"""Placement regression contracts, isolated from institutional records."""
import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_session
from backend.app.auth import get_current_user
from backend.app.main import app
from backend.app.models import Department, FeeRecord, Notification, PlacementDrive, PlacementOutcome, PlacementShortlist, Student, User
from backend.app.placement import api, scoring
from backend.app.placement.schemas import DriveInput, OutcomeInput
from backend.app.placement.service import PlacementError, PlacementService, hard_failures


def drive_input(**changes):
    return DriveInput(**(dict(company='Example', role='Engineer', package_lpa=8,
        drive_date=dt.date.today(), min_attendance=0) | changes))


@pytest.fixture()
def placement_db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(Department(code='AIML', name='AI', intake=60))
        for year in range(1, 5):
            db.add(Student(usn=f'P{year}', name=f'Year {year}', dept_code='AIML', year=year,
                           semester=year * 2, section='A', cgpa=8, backlogs=0, family_income=0))
        db.flush()
        for year in range(1, 5):
            db.add(User(username=f'P{year}', password_hash='test', role='student',
                        display_name=f'Year {year}', usn=f'P{year}', dept_code='AIML'))
        db.commit()
        yield db, factory
    engine.dispose()


@pytest.mark.parametrize('field,value', [('company', ''), ('company', ' '), ('role', ''),
    ('package_lpa', 0), ('package_lpa', float('nan')), ('package_lpa', float('inf')),
    ('min_cgpa', -1), ('min_cgpa', 11), ('max_backlogs', -1), ('max_backlogs', 0.5),
    ('min_attendance', 101), ('status', 'INVALID'), ('departments', 'ALL,AIML'), ('departments', 'AIML,')])
def test_drive_validation(field, value):
    with pytest.raises(ValidationError):
        drive_input(**{field: value})


def test_departments_and_lifecycle(placement_db):
    db, _ = placement_db
    service = PlacementService()
    data = drive_input(departments=' aiml , AIML ', status='DRAFT')
    assert data.departments == 'AIML'
    drive = service.save_drive(db, data)
    with pytest.raises(PlacementError):
        service.generate(db, drive['id'])
    service.save_drive(db, drive_input(status='OPEN'), drive['id'])
    service.transition(db, drive['id'], 'close')
    for operation in [lambda: service.save_drive(db, data, drive['id']), lambda: service.generate(db, drive['id']), lambda: service.transition(db, drive['id'], 'close')]:
        with pytest.raises(PlacementError):
            operation()
    with pytest.raises(PlacementError):
        service.transition(db, drive['id'], 'cancel', 'Withdrawn')
    other = service.save_drive(db, drive_input())
    with pytest.raises(PlacementError, match='reason is required'):
        service.transition(db, other['id'], 'cancel')
    service.transition(db, other['id'], 'cancel', 'Company withdrew')
    with pytest.raises(PlacementError):
        service.generate(db, drive['id'], True)
    with pytest.raises(PlacementError):
        service.save_drive(db, drive_input(departments='UNKNOWN'))


def test_open_drive_notifications_are_final_year_department_scoped_and_deduplicated(placement_db):
    db, _ = placement_db
    db.add(Department(code='CSE', name='Computer Science', intake=60)); db.flush()
    db.add_all([
        Student(usn='CSE4', name='CSE Finalist', dept_code='CSE', year=4, semester=8,
                section='A', cgpa=8, backlogs=0, family_income=0),
        User(username='CSE4', password_hash='test', role='student', display_name='CSE Finalist',
             usn='CSE4', dept_code='CSE')])
    db.flush()
    service = PlacementService()
    record = service.save_drive(db, drive_input(company='Oracle', departments=' aiml , AIML ',
                                application_deadline=dt.date.today()))
    db.commit()
    notices = db.query(Notification).filter_by(notification_type='PLACEMENT_DRIVE_OPENED').all()
    assert {n.usn for n in notices} == {'P4'}
    assert notices[0].route == f"/student/placements/{record['id']}"
    assert 'Engineer' in notices[0].message and '8 LPA' in notices[0].message
    service.save_drive(db, drive_input(company='Oracle updated', departments='AIML'), record['id'])
    db.commit()
    assert db.query(Notification).filter_by(notification_type='PLACEMENT_DRIVE_OPENED').count() == 1


def test_all_departments_and_draft_to_open_notification_lifecycle(placement_db):
    db, _ = placement_db
    db.add(Department(code='CSE', name='Computer Science', intake=60)); db.flush()
    db.add(Student(usn='CSE4', name='CSE Finalist', dept_code='CSE', year=4, semester=8,
                   section='A', cgpa=8, backlogs=0, family_income=0)); db.flush()
    db.add(User(username='CSE4', password_hash='test', role='student', display_name='CSE Finalist',
                usn='CSE4', dept_code='CSE')); db.flush()
    service = PlacementService()
    draft = service.save_drive(db, drive_input(company='Draft Company', status='DRAFT'))
    db.commit()
    assert db.query(Notification).filter_by(notification_type='PLACEMENT_DRIVE_OPENED').count() == 0
    service.save_drive(db, drive_input(company='Draft Company', status='OPEN'), draft['id'])
    db.commit()
    notices = db.query(Notification).filter_by(notification_type='PLACEMENT_DRIVE_OPENED').all()
    assert {n.usn for n in notices} == {'P4', 'CSE4'}


def test_all_hard_filter_reasons_and_no_model_call(placement_db):
    db, _ = placement_db
    student = db.get(Student, 'P4')
    student.cgpa, student.backlogs = 4.5, 3
    drive = PlacementDrive(**drive_input(departments=' cse , ece ', min_attendance=75, requires_fee_clearance=True).model_dump())
    reasons = hard_failures(student, drive, 60, False)
    assert reasons == ['Branch AIML not in eligible list (CSE,ECE)', 'CGPA 4.5 below cutoff of 6',
        '3 active backlog(s) exceeds limit of 0', 'Attendance 60% below requirement of 75%',
        'Outstanding fee dues; this drive requires fee clearance']
    db.add(FeeRecord(usn='P4', fee_type='tuition', amount_due=100, amount_paid=0,
                     due_date=dt.date.today(), status='pending'))
    db.flush()
    model = Mock()
    result = PlacementService(model, 'test').evaluate(db, student, drive, 60)
    assert result['reasons'] == '; '.join(reasons)
    assert not result['eligible'] and result['ml_probability'] is None and result['model_version'] is None
    model.predict_proba.assert_not_called()


@pytest.mark.parametrize('probability,eligible,word', [(0.7, True, 'meets'), (0.5, True, 'meets'), (0.49, False, 'below')])
def test_threshold_and_feature_order(placement_db, monkeypatch, probability, eligible, word):
    monkeypatch.setenv('MAWOS_PLACEMENT_MODEL_THRESHOLD', '0.5')
    db, _ = placement_db
    model = Mock(predict_proba=Mock(return_value=[[1-probability, probability]]))
    result = PlacementService(model, 'rf-test').evaluate(db, db.get(Student, 'P4'), PlacementDrive(**drive_input().model_dump()), 91)
    assert result == dict(eligible=eligible, ml_probability=probability, model_version='rf-test', reasons=f'Model confidence {probability:.2f} {word} threshold 0.5')
    model.predict_proba.assert_called_once_with([[8.0, 0, 91]])
    monkeypatch.setenv('MAWOS_PLACEMENT_MODEL_THRESHOLD', '0.9')
    assert not scoring.score(model, 'rf-test', db.get(Student, 'P4'), 91)['eligible']


@pytest.mark.parametrize('cutoff', ['-1', '2', 'nan', 'bad'])
def test_invalid_threshold_is_controlled(monkeypatch, cutoff):
    monkeypatch.setenv('MAWOS_PLACEMENT_MODEL_THRESHOLD', cutoff)
    with pytest.raises(scoring.config.ConfigurationError):
        scoring.threshold()


@pytest.mark.parametrize('mode', ['missing', 'corrupt', 'wrong-type'])
def test_model_loader_fails_safely(monkeypatch, tmp_path, mode):
    path = tmp_path / 'placement_rf.joblib'
    monkeypatch.setattr(scoring, 'MODEL_PATH', path)
    if mode == 'corrupt':
        path.write_bytes(b'not a pickle')
    if mode == 'wrong-type':
        monkeypatch.setattr(scoring.joblib, 'load', lambda path: object())
    assert scoring.load_model() == (None, None)


def test_trusted_model_compatible_when_present():
    if not scoring.MODEL_PATH.exists():
        pytest.skip('Trusted model not generated in this checkout')
    model, version = scoring.load_model()
    assert model is not None
    assert version.startswith('rf-') and len(version) <= 16


@pytest.mark.parametrize('model', [None, Mock(predict_proba=Mock(side_effect=ValueError('invalid'))), Mock(predict_proba=Mock(return_value=[[0, float('nan')]]))])
def test_rules_only_fallback(placement_db, model):
    db, _ = placement_db
    result = PlacementService(model, None).evaluate(db, db.get(Student, 'P4'), PlacementDrive(**drive_input().model_dump()), 100)
    assert result == dict(eligible=True, ml_probability=None, model_version=None, reasons=scoring.RULES_ONLY)


def test_final_year_scope_upsert_regeneration_and_events(placement_db):
    db, _ = placement_db
    service = PlacementService()
    drive = service.save_drive(db, drive_input())
    result, events = service.generate(db, drive['id'])
    assert result['shortlisted_count'] == 1
    assert db.query(PlacementShortlist).one().usn == 'P4'
    row_id = db.query(PlacementShortlist).one().id
    assert {topic for topic, _ in events} == {'placement.shortlist_generated', 'placement.notification_required'}
    assert events[1][1] == dict(usn='P4', notification_type='PLACEMENT_SHORTLISTED', drive_id=drive['id'])
    with pytest.raises(PlacementError, match='regenerate=true'):
        service.generate(db, drive['id'])
    service.generate(db, drive['id'], True)
    assert db.query(PlacementShortlist).one().id == row_id


def test_existing_automatic_shortlist_requires_explicit_regeneration(placement_db):
    db, _ = placement_db
    service = PlacementService()
    drive = service.save_drive(db, drive_input())
    service.reevaluate_student(db, 'P4')
    with pytest.raises(PlacementError):
        service.generate(db, drive['id'])


@pytest.mark.parametrize('status,days,expected', [('OPEN', -7, 1), ('OPEN', -8, 0), ('SHORTLIST_GENERATED', 50, 1), ('DRAFT', 0, 0), ('CLOSED', 0, 0), ('CANCELLED', 0, 0)])
def test_automatic_window(placement_db, status, days, expected):
    db, _ = placement_db
    drive = PlacementDrive(**drive_input(status=status, drive_date=dt.date.today()+dt.timedelta(days=days)).model_dump())
    db.add(drive); db.flush()
    assert PlacementService().reevaluate_student(db, 'P4') == expected
    assert db.query(PlacementShortlist).count() == expected


@pytest.mark.parametrize('status', ['OFFER_ACCEPTED', 'OFFER_DECLINED', 'REJECTED', 'OFFER_MADE'])
def test_final_outcome_freeze_only_automatic(placement_db, status):
    db, _ = placement_db
    service = PlacementService()
    drive = service.save_drive(db, drive_input())
    service.generate(db, drive['id'])
    outcome, events = service.record_outcome(db, drive['id'], ' p4 ', OutcomeInput(outcome_status=status))
    assert events[0][0] == 'placement.' + status.lower()
    notice = db.query(Notification).filter_by(usn='P4', notification_type='PLACEMENT_OUTCOME').one()
    assert notice.route == f"/student/placements/{drive['id']}"
    db.get(Student, 'P4').cgpa = 1
    db.flush()
    assert service.reevaluate_student(db, 'P4') == (1 if status == 'OFFER_MADE' else 0)
    assert db.query(PlacementShortlist).one().eligible == (status != 'OFFER_MADE')
    service.generate(db, drive['id'], True)
    assert not db.query(PlacementShortlist).one().eligible
    assert service.outcomes(db, drive['id']) == [outcome]


def test_single_accepted_offer_guard_and_override(placement_db):
    db, _ = placement_db
    service = PlacementService()
    a = service.save_drive(db, drive_input())['id']
    b = service.save_drive(db, drive_input())['id']
    accepted = OutcomeInput(outcome_status='OFFER_ACCEPTED')
    service.record_outcome(db, a, 'P4', accepted)
    service.record_outcome(db, a, 'P4', accepted)
    with pytest.raises(PlacementError, match='already has an accepted offer'):
        service.record_outcome(db, b, 'P4', accepted)
    service.record_outcome(db, b, 'P4', accepted.model_copy(update={'allow_multiple_offers': True}))
    assert db.query(PlacementOutcome).count() == 2


def test_preview_is_read_only_no_scoring_and_uses_stored_rows(placement_db):
    db, _ = placement_db
    model = Mock()
    service = PlacementService(model)
    drive = service.save_drive(db, drive_input())
    db.commit()
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', record)
    try:
        result = service.eligibility(db, drive['id'], ' p4 ')
    finally:
        event.remove(db.bind, 'before_cursor_execute', record)
    assert result['status'] == 'NOT_EVALUATED' and result['eligible'] is None
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    model.predict_proba.assert_not_called()
    PlacementService().generate(db, drive['id'])
    db.get(Student, 'P4').cgpa = 0
    db.flush()
    assert service.eligibility(db, drive['id'], 'P4')['eligible'] is True


@pytest.fixture()
def placement_client(placement_db, monkeypatch):
    db, factory = placement_db
    service = PlacementService()
    drive = service.save_drive(db, drive_input())
    db.commit()
    published = []
    async def publish(topic, payload):
        published.append((topic, payload))
    monkeypatch.setattr(api, 'agent', lambda: SimpleNamespace(service=service, publish=publish))
    user = SimpleNamespace(role='admin', usn=None)
    def session():
        with factory() as current:
            yield current
    old = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield TestClient(app), user, drive['id'], published
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(old)


@pytest.mark.parametrize('role', ['student', 'faculty', 'hod', 'principal'])
def test_admin_api_is_role_protected(placement_client, role):
    client, user, drive, _ = placement_client
    user.role, user.usn = role, 'P4'
    root = f'/api/placements/drives/{drive}'
    for method, path, body in [('post', '/api/placements/drives', drive_input().model_dump(mode='json')),
        ('put', root, drive_input().model_dump(mode='json')), ('post', root+'/close', {}),
        ('post', root+'/cancel', {}), ('post', root+'/shortlist', {}),
        ('put', root+'/outcomes/P4', {'outcome_status': 'OFFER_MADE'})]:
        assert getattr(client, method)(path, json=body).status_code == 403
    assert client.get(root+'/shortlist').status_code == 403
    assert client.get(root+'/outcomes').status_code == 403
    assert client.get(root).status_code == (200 if role == 'student' else 403)
    assert client.get('/api/placements/drives').status_code == (200 if role == 'student' else 403)
    if role != 'student':
        assert client.get(root+'/eligibility/P4').status_code == 403


def test_student_scope_and_normalization(placement_client):
    client, user, drive, _ = placement_client
    user.role, user.usn = 'student', 'P4'
    root = f'/api/placements/drives/{drive}/eligibility/'
    assert client.get(root+'%20p4%20').json()['usn'] == 'P4'
    assert client.get(root+'P1').status_code == 403
    assert client.get(root+'NONEXISTENT').status_code == 403
    user.role = 'admin'
    assert client.get(root+'P1').status_code == 200


def test_admin_commands_and_controlled_conflicts(placement_client):
    client, user, drive, events = placement_client
    root = f'/api/placements/drives/{drive}'
    assert client.post(root+'/shortlist', json={}).status_code == 200
    assert client.post(root+'/shortlist', json={}).status_code == 409
    assert client.post(root+'/shortlist', json={'regenerate': True}).status_code == 200
    assert client.put(root+'/outcomes/p4', json={'outcome_status': 'OFFER_ACCEPTED'}).status_code == 200
    assert client.get(root+'/outcomes').json()[0]['usn'] == 'P4'
    assert any(topic == 'placement.offer_accepted' for topic, _ in events)
    assert client.put(root+'/outcomes/p4', json={'outcome_status': 'INVALID'}).status_code == 422
    view = client.get(root+'/admin-view')
    assert view.status_code == 200
    assert view.json()['shortlist'][0]['outcome']['outcome_status'] == 'OFFER_ACCEPTED'


def test_close_and_cancel_are_distinct_api_transitions(placement_client):
    client, user, drive, _ = placement_client
    root = f'/api/placements/drives/{drive}'
    assert client.post(root + '/cancel', json={}).status_code == 422
    response = client.post(root + '/cancel', json={'reason': 'Company withdrew role'})
    assert response.status_code == 200
    assert response.json()['status'] == 'CANCELLED'
    assert response.json()['cancellation_reason'] == 'Company withdrew role'
    assert client.post(root + '/close').status_code == 409


def test_event_subscriptions_and_deduplicated_notifications(placement_db, monkeypatch):
    from backend.app.agents.placement import PlacementAgent
    from backend.app.agents.notification import NotificationAgent
    from backend.app.bus import EventBus
    import backend.app.bus as bus_module
    db, factory = placement_db
    monkeypatch.setattr(scoring, 'load_model', lambda: (None, None))
    monkeypatch.setattr(bus_module, 'SessionLocal', factory)
    bus = EventBus()
    agent = PlacementAgent(bus)
    notice = NotificationAgent(bus)
    monkeypatch.setattr(agent, 'session', factory)
    monkeypatch.setattr(notice, 'session', factory)
    drive = agent.service.save_drive(db, drive_input())
    db.commit()
    for topic in ('attendance.updated', 'fees.updated'):
        asyncio.run(bus.publish(topic, {'usns': ['P4', 'P1']}))
    db.expire_all()
    assert db.query(PlacementShortlist).count() == 1
    from backend.app.models import WorkflowEvent
    updates = db.query(WorkflowEvent).filter_by(topic='placement.updated').all()
    import json
    assert all(json.loads(row.payload)['usns'] == ['P4'] for row in updates)
    for _ in range(2):
        _, events = agent.service.generate(db, drive['id'], True)
        db.commit()
        for topic, payload in events:
            asyncio.run(bus.publish(topic, payload))
    assert db.query(Notification).filter_by(usn='P4', notification_type='PLACEMENT_SHORTLISTED').count() == 1
    assert db.query(Notification).filter_by(usn='P1').count() == 0
