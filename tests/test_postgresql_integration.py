"""Guarded integration checks for a separate PostgreSQL database only.

Set MAWOS_POSTGRES_TEST_URL to a Psycopg URL for database `mawos_test` before
running these checks. They never use MAWOS_DATABASE_URL, which may point at the
populated `mawos` database.
"""
import importlib.util
import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app.auth import create_token, hash_password
from backend.app.database import Base, get_session
from backend.app.main import app
from backend.app.models import FeeRecord, User
from backend.app.postgresql_safety import (
    PostgreSQLSafetyError,
    isolated_test_database_url,
    require_test_database_name,
)


def _postgres_test_url() -> str:
    if not os.getenv("MAWOS_POSTGRES_TEST_URL"):
        pytest.skip("MAWOS_POSTGRES_TEST_URL is not configured; PostgreSQL tests stay disabled")
    try:
        return isolated_test_database_url(
            os.getenv("MAWOS_LIVE_DATABASE_URL_FOR_TEST_GUARD"),
            os.getenv("MAWOS_POSTGRES_TEST_URL"),
        ).render_as_string(hide_password=False)
    except PostgreSQLSafetyError as exc:
        pytest.fail(str(exc))


@pytest.fixture(scope="module")
def postgres_engine():
    engine = create_engine(_postgres_test_url(), pool_pre_ping=True, future=True)
    try:
        with engine.connect() as connection:
            database = connection.execute(text("SELECT current_database()")).scalar_one()
            try:
                require_test_database_name(database)
            except PostgreSQLSafetyError as exc:
                pytest.fail(str(exc))
        yield engine
    finally:
        engine.dispose()


def test_psycopg_driver_is_available():
    assert importlib.util.find_spec("psycopg") is not None


def test_postgresql_connection_dialect_and_schema_discovery(postgres_engine):
    with postgres_engine.connect() as connection:
        connection.execute(text("BEGIN TRANSACTION READ ONLY"))
        assert postgres_engine.dialect.name == "postgresql"
        assert "users" in inspect(connection).get_table_names(schema="public")
        connection.rollback()


def test_postgresql_session_commit_rollback_and_constraints(postgres_engine):
    """Use a dedicated test DB and delete only the user created by this test."""
    Session = sessionmaker(bind=postgres_engine, autoflush=False, future=True)
    username = f"integration.{uuid.uuid4().hex}"
    session = Session()
    try:
        user = User(username=username, password_hash=hash_password("test"),
                    role="student", display_name="PostgreSQL Integration")
        session.add(user)
        session.commit()
        assert session.query(User).filter_by(username=username).one().id is not None

        session.add(User(username=username, password_hash=hash_password("test"),
                         role="student", display_name="Duplicate"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(FeeRecord(usn="missing-student", fee_type="test", amount_due=1,
                              due_date="2026-01-01", status="pending"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    finally:
        with postgres_engine.connect() as connection:
            database = connection.execute(text("SELECT current_database()")).scalar_one()
            require_test_database_name(database)
        session.query(User).filter_by(username=username).delete()
        session.commit()
        session.close()


def test_basic_authenticated_api_query_uses_postgresql_session(postgres_engine):
    Session = sessionmaker(bind=postgres_engine, autoflush=False, future=True)
    session = Session()
    username = f"api.integration.{uuid.uuid4().hex}"
    user = User(username=username, password_hash=hash_password("test"),
                role="student", display_name="PostgreSQL API")
    session.add(user)
    session.commit()

    def override_session():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_session] = override_session
    try:
        from fastapi.testclient import TestClient

        response = TestClient(app).get(
            "/api/me", headers={"Authorization": f"Bearer {create_token(user)}"}
        )
        assert response.status_code == 200
        assert response.json()["username"] == username
    finally:
        app.dependency_overrides.pop(get_session, None)
        with postgres_engine.connect() as connection:
            database = connection.execute(text("SELECT current_database()")).scalar_one()
            require_test_database_name(database)
        session.query(User).filter_by(username=username).delete()
        session.commit()
        session.close()


def test_empty_postgresql_database_migrates_to_head_and_matches_runtime_schema():
    """Exercise the supported clean-database path only on the guarded test server."""
    source = _postgres_test_url()
    source_url = make_url(source)
    name = f"mawos_fresh_{uuid.uuid4().hex[:20]}"
    fresh_url = source_url.set(database=name)
    admin_engine = create_engine(source_url, isolation_level="AUTOCOMMIT", future=True)
    fresh_engine = None
    from backend.app import config as app_config
    from backend.app import database as app_database
    original_url, original_engine = app_config.DATABASE_URL, app_database.engine
    try:
        with admin_engine.connect() as connection:
            database = connection.execute(text("SELECT current_database()")).scalar_one()
            require_test_database_name(database)
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        app_config.DATABASE_URL = fresh_url.render_as_string(hide_password=False)
        from alembic import command
        from alembic.config import Config
        alembic_config = Config(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(alembic_config, "head")
        fresh_engine = create_engine(fresh_url, future=True)
        required = set(Base.metadata.tables)
        assert required <= set(inspect(fresh_engine).get_table_names(schema="public"))
        app_database.engine = fresh_engine
        app_database.verify_existing_schema()
    finally:
        app_config.DATABASE_URL = original_url
        app_database.engine = original_engine
        if fresh_engine is not None:
            fresh_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"), {"name": name})
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin_engine.dispose()


def test_postgresql_upgrade_from_timetable_operations_to_head_accepts_long_revision_id():
    """The 20260923 revision must widen Alembic's default VARCHAR(32) first."""
    source = _postgres_test_url()
    source_url = make_url(source)
    name = f"mawos_upgrade_{uuid.uuid4().hex[:20]}"
    fresh_url = source_url.set(database=name)
    admin_engine = create_engine(source_url, isolation_level="AUTOCOMMIT", future=True)
    fresh_engine = None
    from backend.app import config as app_config
    original_url = app_config.DATABASE_URL
    try:
        with admin_engine.connect() as connection:
            require_test_database_name(connection.execute(text("SELECT current_database()")).scalar_one())
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        app_config.DATABASE_URL = fresh_url.render_as_string(hide_password=False)
        from alembic import command
        from alembic.config import Config
        alembic_config = Config(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(alembic_config, "20260921_timetable_operations")
        command.upgrade(alembic_config, "head")
        fresh_engine = create_engine(fresh_url, future=True)
        with fresh_engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == "20260923_validate_replacement_periods"
            size = connection.execute(text("""
                SELECT character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'alembic_version'
                  AND column_name = 'version_num'
            """)).scalar_one()
            assert size >= 64
    finally:
        app_config.DATABASE_URL = original_url
        if fresh_engine is not None:
            fresh_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"), {"name": name})
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin_engine.dispose()
