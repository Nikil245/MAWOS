"""Real PostgreSQL constraints, rollback and competing publications on mawos_test."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from fastapi import HTTPException
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from backend.app.models import Department, Faculty, Student, Subject, TeachingAssignment, User
from backend.app.postgresql_safety import require_test_database_name
from backend.app.timetable import models as m, service as s
from backend.app.timetable.bootstrap import BootstrapOptions, bootstrap
from backend.app.timetable.solver import solve
from test_postgresql_integration import postgres_engine  # Guarded existing fixture.
from timetable_fixtures import college


@pytest.fixture()
def pg(postgres_engine):
    factory = sessionmaker(bind=postgres_engine, autoflush=False, expire_on_commit=False)
    db = factory()
    data = college(db)
    data.update(db=db, factory=factory, engine=postgres_engine)
    yield data
    db.rollback()
    # Delete only the explicit fixture namespace, respecting every FK.
    with factory() as clean:
        require_test_database_name(clean.execute(text('SELECT current_database()')).scalar_one())
        term_id, departments = data['term'].id, [data['dept'], data['other']]
        faculty_ids = [f.id for f in clean.query(Faculty).filter(Faculty.dept_code.in_(departments))]
        section_ids = [r.id for r in clean.query(m.Section).filter_by(term_id=term_id)]
        room_ids = [r.id for r in clean.query(m.Room).filter(m.Room.dept_code.in_(departments))]
        periods = [r.id for r in clean.query(m.PeriodDefinition).filter_by(term_id=term_id)]
        clean.query(m.Audit).filter(m.Audit.term_id == term_id).delete(synchronize_session=False)
        clean.query(m.Audit).filter(m.Audit.dept_code.in_(departments)).delete(synchronize_session=False)
        clean.query(m.Entry).filter_by(term_id=term_id).delete()
        clean.query(m.Run).filter_by(term_id=term_id).update({'parent_run_id': None})
        clean.query(m.Run).filter_by(term_id=term_id).delete()
        clean.query(m.Requirement).filter(m.Requirement.section_id.in_(section_ids)).delete(synchronize_session=False)
        clean.query(m.FacultyUnavailable).filter(m.FacultyUnavailable.period_id.in_(periods)).delete(synchronize_session=False)
        clean.query(m.RoomUnavailable).filter(m.RoomUnavailable.period_id.in_(periods)).delete(synchronize_session=False)
        clean.query(m.FacultyLimit).filter_by(term_id=term_id).delete()
        clean.query(m.Qualification).filter(m.Qualification.faculty_id.in_(faculty_ids)).delete(synchronize_session=False)
        clean.query(m.Holiday).filter_by(term_id=term_id).delete()
        clean.query(m.PeriodDefinition).filter_by(term_id=term_id).delete()
        clean.query(m.Section).filter_by(term_id=term_id).delete()
        clean.query(m.Term).filter_by(id=term_id).delete()
        clean.query(m.Room).filter(m.Room.id.in_(room_ids)).delete(synchronize_session=False)
        clean.query(TeachingAssignment).filter(TeachingAssignment.dept_code.in_(departments)).delete(synchronize_session=False)
        clean.query(User).filter(User.dept_code.in_(departments)).delete(synchronize_session=False)
        clean.query(Student).filter(Student.dept_code.in_(departments)).delete(synchronize_session=False)
        clean.query(Subject).filter(Subject.dept_code.in_(departments)).delete(synchronize_session=False)
        clean.query(Faculty).filter(Faculty.dept_code.in_(departments)).delete(synchronize_session=False)
        clean.query(Department).filter(Department.code.in_(departments)).delete(synchronize_session=False)
        clean.commit()
    db.close()


def draft(pg, seed=7):
    db = pg['db']
    data, bundle, issues = s.snapshot(db, pg['term'].id, pg['dept'])
    assert not issues
    result = solve(data, seed=seed)
    return s.persist(db, pg['users']['hod'], pg['term'].id, pg['dept'], seed, data, bundle, result, 1)


def test_postgresql_bootstrap_is_idempotent_isolated_and_preserves_sources(pg, monkeypatch):
    db = pg['db']
    db.query(m.Requirement).filter_by(section_id=pg['section'].id).delete()
    db.query(m.Qualification).filter_by(faculty_id=pg['faculty'].id).delete()
    db.query(m.FacultyLimit).filter_by(faculty_id=pg['faculty'].id, term_id=pg['term'].id).delete()
    db.query(m.Section).filter_by(id=pg['section'].id).delete()
    db.commit()
    academic_before = {model: db.query(model).count()
                       for model in (Department, Faculty, Student, Subject, TeachingAssignment)}
    versions_before = (db.query(m.Run).count(), db.query(m.Entry).count())
    monkeypatch.setenv('MAWOS_ALLOW_TIMETABLE_BOOTSTRAP', 'true')
    options = BootstrapOptions(use_subject_credits=True, room_type='classroom',
        faculty_daily_limit=4, faculty_weekly_limit=20)
    preview = bootstrap(db, pg['term'].id, departments=[pg['dept']], options=options)
    assert preview['missing_faculty_qualifications']
    assert preview['created'] == {}
    first = bootstrap(db, pg['term'].id, departments=[pg['dept']], apply=True, options=options)
    db.commit()
    assert first['created'] == {'sections': 1, 'qualifications': 1,
                                'faculty_limits': 1, 'requirements': 1}
    counts = tuple(db.query(model).count() for model in
                   (m.Section, m.Qualification, m.FacultyLimit, m.Requirement))
    second = bootstrap(db, pg['term'].id, departments=[pg['dept']], apply=True, options=options)
    db.commit()
    assert second['created'] == {}
    assert counts == tuple(db.query(model).count() for model in
                           (m.Section, m.Qualification, m.FacultyLimit, m.Requirement))
    assert db.get(m.Qualification, (pg['faculty'].id, pg['subject'].code)) is not None
    requirement = db.query(m.Requirement).join(m.Section).filter(
        m.Section.dept_code == pg['dept']).one()
    assignment = db.get(TeachingAssignment, requirement.assignment_id)
    assert (assignment.faculty_id, assignment.subject_code) == (pg['faculty'].id, pg['subject'].code)
    assert db.query(m.Section).filter_by(term_id=pg['term'].id, dept_code=pg['other']).count() == 1
    assert academic_before == {model: db.query(model).count() for model in academic_before}
    assert versions_before == (db.query(m.Run).count(), db.query(m.Entry).count())


def test_postgresql_bootstrap_reports_missing_subject_rooms_and_weekly_values(pg, monkeypatch):
    pg['db'].query(m.Requirement).filter_by(section_id=pg['section'].id).delete()
    pg['assignment'].section = 'B'
    pg['db'].flush()
    report = bootstrap(pg['db'], pg['term'].id, departments=[pg['dept']],
                       options=BootstrapOptions(room_type='special_lab'))
    assert report['missing_subject_mappings']
    pg['assignment'].section = 'A'
    pg['db'].flush()
    report = bootstrap(pg['db'], pg['term'].id, departments=[pg['dept']],
                       options=BootstrapOptions(room_type='special_lab'))
    assert report['missing_weekly_period_values']
    assert report['missing_rooms']
    assert {'missing_weekly_periods', 'missing_room'} <= {x['code'] for x in report['conflicts']}
    with pytest.raises(ValueError, match='explicit confirmation'):
        BootstrapOptions(create_placeholder_rooms=True).validate()
    monkeypatch.setenv('MAWOS_ALLOW_TIMETABLE_BOOTSTRAP', 'true')
    placeholder = bootstrap(pg['db'], pg['term'].id, departments=[pg['dept']], apply=True,
        options=BootstrapOptions(room_type='special_lab', create_placeholder_rooms=True,
                                 confirm_placeholder_rooms=True))
    pg['db'].flush()
    assert placeholder['created']['placeholder_rooms'] == 1
    assert placeholder['placeholders'][0]['name'].startswith('PLACEHOLDER-')


def test_migrated_postgresql_has_collision_scope_and_publication_constraints(postgres_engine):
    inspector = inspect(postgres_engine)
    uniques = {c['name'] for c in inspector.get_unique_constraints('tt_entries')}
    assert {'uq_tt_entry_section', 'uq_tt_entry_faculty', 'uq_tt_entry_room'} <= uniques
    fks = {c['name'] for c in inspector.get_foreign_keys('tt_entries')}
    assert {'fk_tt_entry_run_scope', 'fk_tt_entry_section_scope', 'fk_tt_entry_period'} <= fks
    index = next(i for i in inspector.get_indexes('tt_runs') if i['name'] == 'uq_tt_published_scope')
    assert index['unique'] and 'PUBLISHED' in str(index['dialect_options'])


@pytest.mark.parametrize('resource', ['section', 'faculty', 'room'])
def test_postgresql_rejects_each_resource_collision(pg, resource):
    run = draft(pg)
    db = pg['db']
    first = db.query(m.Entry).filter_by(run_id=run['id']).first()
    sec = m.Section(term_id=pg['term'].id, dept_code=pg['dept'], year=3, semester=5, name='B', size=30)
    room = m.Room(dept_code=pg['dept'], name='Extra '+pg['dept'], kind='classroom', capacity=40)
    db.add_all([sec, room])
    db.commit()
    values = {column.name: getattr(first, column.name) for column in m.Entry.__table__.columns if column.name != 'id'}
    if resource != 'section': values['section_id'] = sec.id
    if resource != 'faculty': values['faculty_id'] = pg['outside'].id
    if resource != 'room': values['room_id'] = room.id
    db.add(m.Entry(**values))
    with pytest.raises(IntegrityError) as exc:
        db.flush()
    assert exc.value.orig.diag.constraint_name == f'uq_tt_entry_{resource}'
    db.rollback()


def test_postgresql_rejects_mismatched_entry_scope_and_duplicate_publication(pg):
    run = draft(pg)
    db = pg['db']
    first = db.query(m.Entry).filter_by(run_id=run['id']).first()
    first.dept_code = pg['other']
    with pytest.raises(IntegrityError): db.flush()
    db.rollback()
    second = draft(pg, seed=8)
    s.publish(db, pg['users']['hod'], run['id'])
    db.get(m.Run, second['id']).status = 'PUBLISHED'
    with pytest.raises(IntegrityError) as exc: db.flush()
    assert exc.value.orig.diag.constraint_name == 'uq_tt_published_scope'
    db.rollback()


def test_postgresql_generation_failure_rolls_back_run_entries_and_audit(pg, monkeypatch):
    data, bundle, issues = s.snapshot(pg['db'], pg['term'].id, pg['dept'])
    assert not issues
    def fail(*a, **kw): raise RuntimeError('injected persistence error')
    monkeypatch.setattr(s, 'audit', fail)
    with pytest.raises(HTTPException) as exc:
        s.persist(pg['db'], pg['users']['hod'], pg['term'].id, pg['dept'], 7, data, bundle, solve(data), 1)
    assert exc.value.status_code == 500
    for model in (m.Run, m.Entry, m.Audit):
        assert pg['db'].query(model).filter_by(term_id=pg['term'].id).count() == 0


def test_postgresql_publication_failure_restores_previous_version(pg, monkeypatch):
    a, b = draft(pg), draft(pg, seed=8)
    db, user = pg['db'], pg['users']['hod']
    s.publish(db, user, a['id'])
    original = s.audit
    def failure(db, user, event, **kw):
        if event == 'timetable.published': raise RuntimeError('injected publication error')
        return original(db, user, event, **kw)
    monkeypatch.setattr(s, 'audit', failure)
    with pytest.raises(HTTPException): s.publish(db, user, b['id'])
    db.expire_all()
    assert db.get(m.Run, a['id']).status == 'PUBLISHED'
    assert db.get(m.Run, b['id']).status == 'COMPLETE'
    assert db.query(m.Audit).filter_by(term_id=pg['term'].id, event='timetable.archived').count() == 0


@pytest.mark.parametrize('same_run', [False, True])
def test_concurrent_publishers_leave_exactly_one_published_version(pg, same_run):
    a, b = draft(pg), draft(pg, seed=91)
    user_id, term_id = pg['users']['hod'].id, pg['term'].id
    barrier = threading.Barrier(2)
    def worker(run_id):
        with pg['factory']() as session:
            user = session.get(User, user_id)
            barrier.wait(timeout=10)
            try:
                return s.publish(session, user, run_id)['status']
            except HTTPException as exc:
                return exc.status_code
    # End any fixture read transaction before competing sessions acquire their locks.
    pg['db'].rollback()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, rid) for rid in (a['id'], a['id'] if same_run else b['id'])]
        results = [f.result(timeout=20) for f in futures]
    assert results.count('PUBLISHED') == (1 if same_run else 2)
    if same_run: assert 409 in results
    with pg['factory']() as session:
        assert session.query(m.Run).filter_by(term_id=term_id, dept_code=pg['dept'], status='PUBLISHED').count() == 1
        assert session.query(m.Entry).filter_by(term_id=term_id).count() == 8


def test_personal_http_gets_work_in_postgresql_read_only_transaction(pg):
    from fastapi.testclient import TestClient
    from backend.app.auth import create_token
    from backend.app.database import get_session
    from backend.app.main import app
    run = draft(pg)
    s.publish(pg['db'], pg['users']['hod'], run['id'])
    tokens = {role: create_token(pg['users'][role]) for role in ('student', 'faculty')}
    def readonly_session():
        with pg['factory']() as session:
            session.execute(text('SET TRANSACTION READ ONLY'))
            def forbidden(*args, **kwargs): raise AssertionError('GET attempted flush or commit')
            session.commit = forbidden
            session.flush = forbidden
            yield session
    app.dependency_overrides[get_session] = readonly_session
    try:
        client = TestClient(app)
        for role in ('student', 'faculty'):
            for suffix in ('', '/weekly', '/today', '/current-next'):
                result = client.get(f'/api/{role}/timetable{suffix}', headers={'Authorization': f'Bearer {tokens[role]}'})
                assert result.status_code == 200
                assert result.json()['published'] and len(result.json()['weekly']) == 4
    finally:
        app.dependency_overrides.pop(get_session, None)
