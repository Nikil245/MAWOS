"""Real PostgreSQL locks/migration; only explicitly configured mawos_test is allowed."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.library import service as s
from backend.app.models import Book, BookReservation, Department, Student, User, Notification
from backend.app.postgresql_safety import require_test_database_name

LIBRARY_TABLES = {'books', 'book_departments', 'book_reservations', 'book_issues',
                  'book_reviews', 'library_fines', 'librarian_accounts'}


@pytest.fixture
def postgres_library():
    configured = os.getenv('MAWOS_POSTGRES_TEST_URL')
    if not configured:
        pytest.skip('Set MAWOS_POSTGRES_TEST_URL to the isolated mawos_test database')
    url = make_url(configured)
    require_test_database_name(url.database)
    if url.drivername != 'postgresql+psycopg':
        pytest.fail('Library locking tests require postgresql+psycopg')
    root = create_engine(url)
    schema = 'library_test_' + uuid.uuid4().hex
    with root.begin() as connection:
        require_test_database_name(connection.execute(text('SELECT current_database()')).scalar_one())
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(url, isolation_level='READ COMMITTED', connect_args={'options': f'-csearch_path={schema}'})
    try:
        # New migration creates library tables; existing structures model the actual predecessor.
        Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name not in LIBRARY_TABLES])
        path = Path(__file__).resolve().parents[1] / 'alembic/versions/20260915_library.py'
        spec = importlib.util.spec_from_file_location('library_migration', path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        yield engine, migration
    finally:
        engine.dispose()
        with root.begin() as connection:
            require_test_database_name(connection.execute(text('SELECT current_database()')).scalar_one())
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        root.dispose()


def seed(engine):
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with sessions.begin() as db:
        db.add(Department(code='LIBPG', name='Library PG', intake=60)); db.flush()
        db.add_all([Student(usn=f'LIBPG{i}', name=f'Student {i}', dept_code='LIBPG', year=3, semester=5, cgpa=8) for i in (1, 2)])
        db.flush()
        db.add_all([User(username=f'LIBPG{i}', password_hash='test-only', display_name=f'Student {i}', role='student', usn=f'LIBPG{i}') for i in (1, 2)])
        book = Book(isbn='9780000000001', title='Last copy', author='Author', category='PG', total_copies=1, available_copies=1)
        db.add(book); db.flush(); book_id = book.id
    return sessions, book_id


def test_library_migration_upgrade_downgrade_upgrade_retains_history(postgres_library):
    engine, migration = postgres_library; sessions, book_id = seed(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade(); migration.upgrade()
        assert connection.execute(text('SELECT count(*) FROM books')).scalar_one() == 1
        assert LIBRARY_TABLES <= set(inspect(connection).get_table_names())
        assert 'uq_book_reservations_pending' in {item['name'] for item in inspect(connection).get_indexes('book_reservations')}
        with pytest.raises(IntegrityError):
            with connection.begin_nested(): connection.execute(text('UPDATE books SET available_copies=2'))
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("INSERT INTO users(username,password_hash,role,display_name) VALUES ('bad','hash','unrecognized','bad')"))
    with sessions.begin() as db:
        reservation = s.reserve(db, 'LIBPG1', book_id); reservation_id = reservation.id
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE book_reservations SET slip_code='12345X' WHERE id=:id"), {'id': reservation_id})
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("INSERT INTO book_reservations(student_usn,book_id,slip_code,status,requested_at,pickup_deadline) SELECT student_usn,book_id,'000000',status,requested_at,pickup_deadline FROM book_reservations WHERE id=:id"), {'id': reservation_id})


def test_concurrent_last_copy_reservations_only_one_succeeds(postgres_library):
    engine, _ = postgres_library; sessions, book_id = seed(engine)
    barrier = Barrier(2)
    def attempt(usn):
        with sessions() as db:
            barrier.wait(timeout=10)
            try:
                row = s.reserve(db, usn, book_id); db.commit(); return ('ok', row.id)
            except HTTPException as error:
                db.rollback(); return ('conflict', error.status_code)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ['LIBPG1', 'LIBPG2']))
    assert sorted(result[0] for result in results) == ['conflict', 'ok']
    assert [result[1] for result in results if result[0] == 'conflict'] == [409]
    with sessions() as db:
        assert db.get(Book, book_id).available_copies == 0
        assert db.query(BookReservation).filter_by(status='PENDING_PICKUP').count() == 1
        assert db.query(Notification).filter_by(notification_type='reserved').count() == 1


def test_concurrent_pickup_return_and_expiry_are_idempotent(postgres_library):
    import datetime as dt
    from backend.app.models import BookIssue, LibraryFine
    engine, _ = postgres_library; sessions, book_id = seed(engine)
    with sessions.begin() as db:
        reservation_id = s.reserve(db, 'LIBPG1', book_id).id
    def parallel(operation):
        barrier = Barrier(2)
        def run(_):
            with sessions.begin() as db:
                barrier.wait(timeout=10)
                return operation(db)
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, [0, 1]))
    ids = parallel(lambda db: s.pickup(db, reservation_id).id)
    assert ids[0] == ids[1]
    with sessions.begin() as db:
        row = db.get(BookIssue, ids[0]); row.issued_at = s.utcnow() - dt.timedelta(days=12); row.due_at = s.utcnow() - dt.timedelta(days=5)
    parallel(lambda db: s.confirm_return(db, ids[0]).id)
    with sessions.begin() as db:
        assert db.get(Book, book_id).available_copies == 1
        assert db.query(LibraryFine).filter_by(source_type='OVERDUE_RETURN', source_id=ids[0]).count() == 1
        reservation = s.reserve(db, 'LIBPG2', book_id)
        reservation.requested_at = s.utcnow() - dt.timedelta(days=3); reservation.pickup_deadline = s.utcnow() - dt.timedelta(days=1)
        expired_id = reservation.id
    def expire(db):
        row, book = s.workflow_lock(db, BookReservation, expired_id)
        return s.expire_locked(db, row, book, s.utcnow())
    assert sorted(parallel(expire)) == [False, True]
    with sessions() as db:
        assert db.get(Book, book_id).available_copies == 1
        assert db.query(LibraryFine).filter_by(source_type='MISSED_PICKUP', source_id=expired_id).count() == 1
        assert db.query(Notification).filter_by(notification_type='issued').count() == 1
        assert db.query(Notification).filter_by(notification_type='return_confirmed').count() == 1
        assert db.query(Notification).filter_by(notification_type='reservation_expired').count() == 1
