"""Real migration/locking tests confined to unique schemas in mawos_test.

The rollback of each test transaction removes only its own temporary schema.
The concurrency test retains its uniquely named test schema for inspection.
"""
import concurrent.futures
import importlib.util
import os
from pathlib import Path
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models import Department, Student, PlacementOutcome
from backend.app.postgresql_safety import require_test_database_name
from backend.app.placement.schemas import OutcomeInput
from backend.app.placement.service import PlacementError, PlacementService
from test_placement import drive_input


@pytest.fixture()
def pg_engine():
    configured = os.getenv('MAWOS_POSTGRES_TEST_URL')
    if not configured:
        pytest.skip('Set MAWOS_POSTGRES_TEST_URL to the isolated mawos_test database')
    url = make_url(configured)
    require_test_database_name(url.database)
    if url.drivername != 'postgresql+psycopg':
        pytest.fail('Use Psycopg 3 for placement PostgreSQL tests')
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        require_test_database_name(connection.execute(text('SELECT current_database()')).scalar_one())
    yield engine
    engine.dispose()


def test_migration_upgrade_downgrade_upgrade_preserves_legacy_and_new_rows(pg_engine):
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/20260911_placement.py'
    spec = importlib.util.spec_from_file_location('placement_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    details_path = Path(__file__).resolve().parents[1] / 'alembic/versions/20260912_placement_details.py'
    details_spec = importlib.util.spec_from_file_location('placement_details_migration', details_path)
    details = importlib.util.module_from_spec(details_spec)
    details_spec.loader.exec_module(details)
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        schema = 'placement_migration_' + uuid.uuid4().hex
        conn.execute(text(f'CREATE SCHEMA {schema}'))
        conn.execute(text(f'SET LOCAL search_path TO {schema}'))
        conn.execute(text('CREATE TABLE students (usn varchar(16) PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE users (id serial PRIMARY KEY)'))
        conn.execute(text('''CREATE TABLE placement_drives (id serial PRIMARY KEY,
            company varchar(128) NOT NULL, role varchar(128) NOT NULL,
            package_lpa float NOT NULL, min_cgpa float NOT NULL DEFAULT 6,
            max_backlogs integer NOT NULL DEFAULT 0, min_attendance float NOT NULL DEFAULT 75,
            drive_date date NOT NULL, departments varchar(64) NOT NULL DEFAULT 'ALL')'''))
        conn.execute(text('''CREATE TABLE placement_shortlists (id serial PRIMARY KEY,
            drive_id integer NOT NULL REFERENCES placement_drives(id),
            usn varchar(16) NOT NULL REFERENCES students(usn), eligible boolean NOT NULL,
            ml_probability float, reasons text NOT NULL DEFAULT '', updated_at timestamp,
            CONSTRAINT uq_shortlist_entry UNIQUE (drive_id, usn))'''))
        conn.execute(text("INSERT INTO students VALUES ('PG4')"))
        conn.execute(text("INSERT INTO placement_drives(company, role, package_lpa, drive_date) VALUES ('Existing', 'Engineer', 8, CURRENT_DATE)"))
        conn.execute(text("INSERT INTO placement_shortlists(drive_id, usn, eligible, reasons) VALUES (1, 'PG4', true, 'legacy reason')"))
        context = MigrationContext.configure(conn)
        try:
            with Operations.context(context):
                migration.upgrade()
                details.upgrade()
                assert conn.execute(text('SELECT status FROM placement_drives')).scalar_one() == 'OPEN'
                assert conn.execute(text('SELECT requires_fee_clearance FROM placement_drives')).scalar_one() is False
                conn.execute(text("INSERT INTO placement_outcomes(drive_id, usn, outcome_status, decided_at, updated_at) VALUES (1, 'PG4', 'OFFER_ACCEPTED', now(), now())"))
                conn.execute(text("UPDATE placement_drives SET description='Preserved details', application_url='https://example.com/jobs/1'"))
                details.downgrade()
                migration.downgrade()
                migration.upgrade()
                details.upgrade()
            assert conn.execute(text('SELECT reasons FROM placement_shortlists')).scalar_one() == 'legacy reason'
            assert conn.execute(text('SELECT outcome_status FROM placement_outcomes')).scalar_one() == 'OFFER_ACCEPTED'
            assert conn.execute(text('SELECT company FROM placement_drives')).scalar_one() == 'Existing'
            assert conn.execute(text('SELECT description FROM placement_drives')).scalar_one() == 'Preserved details'
            assert conn.execute(text('SELECT count(*) FROM placement_shortlists')).scalar_one() == 1
            from sqlalchemy import inspect
            assert {'ix_placement_drive_status_date'} <= {i['name'] for i in inspect(conn).get_indexes('placement_drives')}
            assert any(set(c['column_names']) == {'drive_id', 'usn'} for c in inspect(conn).get_unique_constraints('placement_outcomes'))
        finally:
            transaction.rollback()


def test_legacy_status_reconciliation_is_scoped_audited_and_idempotent(pg_engine):
    path = Path(__file__).resolve().parents[1] / 'scripts/reconcile_placement_statuses.py'
    spec = importlib.util.spec_from_file_location('placement_reconcile', path)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    schema = 'placement_reconcile_' + uuid.uuid4().hex
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(text(f'CREATE SCHEMA {schema}'))
        conn.execute(text(f'SET LOCAL search_path TO {schema}'))
        Base.metadata.create_all(conn)
        conn.execute(text("INSERT INTO departments(code,name,intake) VALUES ('AIML','AI',1)"))
        conn.execute(text("INSERT INTO students(usn,name,dept_code,year,semester,section,cgpa,backlogs,category,family_income,admission_year,status,is_synthetic) VALUES ('PG4','Test','AIML',4,8,'A',8,0,'GM',0,2023,'enrolled',true)"))
        conn.execute(text("INSERT INTO placement_drives(company,role,package_lpa,min_cgpa,max_backlogs,min_attendance,drive_date,departments,status) VALUES ('Open with rows','Engineer',8,6,0,75,current_date,'ALL','OPEN'),('Open empty','Engineer',8,6,0,75,current_date,'ALL','OPEN'),('Draft','Engineer',8,6,0,75,current_date,'ALL','DRAFT'),('Closed','Engineer',8,6,0,75,current_date,'ALL','CLOSED')"))
        conn.execute(text("INSERT INTO placement_shortlists(drive_id,usn,eligible,reasons) VALUES (1,'PG4',true,'unchanged')"))
        assert command.reconcile(conn) == [1]
        assert command.reconcile(conn) == []
        statuses = dict(conn.execute(text('SELECT company,status FROM placement_drives')).all())
        assert statuses == {'Open with rows': 'SHORTLIST_GENERATED', 'Open empty': 'OPEN', 'Draft': 'DRAFT', 'Closed': 'CLOSED'}
        assert conn.execute(text("SELECT count(*) FROM workflow_events WHERE topic='placement.status_reconciled'")).scalar_one() == 1
        assert conn.execute(text('SELECT reasons FROM placement_shortlists')).scalar_one() == 'unchanged'
        transaction.rollback()


def test_concurrent_shortlists_and_single_offer_acceptance(pg_engine):
    # Unique schema isolates this test from all existing mawos_test records.
    schema = 'placement_concurrent_' + uuid.uuid4().hex
    with pg_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
        conn.execute(text(f'SET LOCAL search_path TO {schema}'))
        Base.metadata.create_all(conn)
    engine = create_engine(pg_engine.url, connect_args={'options': f'-csearch_path={schema}'})
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    service = PlacementService()
    try:
        with factory() as db:
            db.add(Department(code='AIML', name='Test', intake=1)); db.flush()
            db.add(Student(usn='PG4', name='Test finalist', dept_code='AIML', year=4,
                           semester=8, section='A', cgpa=9, backlogs=0, family_income=0)); db.flush()
            first = service.save_drive(db, drive_input())['id']
            second = service.save_drive(db, drive_input())['id']
            db.commit()
        def generate(_):
            with factory() as db:
                try:
                    service.generate(db, first)
                    db.commit()
                    return 'ok'
                except PlacementError:
                    db.rollback()
                    return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            assert sorted(pool.map(generate, [1, 2])) == ['conflict', 'ok']
        def accept(drive):
            with factory() as db:
                try:
                    service.record_outcome(db, drive, 'PG4', OutcomeInput(outcome_status='OFFER_ACCEPTED'))
                    db.commit()
                    return 'ok'
                except PlacementError:
                    db.rollback()
                    return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            assert sorted(pool.map(accept, [first, second])) == ['conflict', 'ok']
        with factory() as db:
            assert db.query(PlacementOutcome).filter_by(outcome_status='OFFER_ACCEPTED').count() == 1
            from backend.app.models import PlacementShortlist
            assert db.query(PlacementShortlist).count() == 1
    finally:
        engine.dispose()
