"""Parent migration checks confined to a temporary schema in mawos_test."""
import importlib.util
import os
from pathlib import Path
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from backend.app.postgresql_safety import require_test_database_name


def test_parent_migration_upgrade_downgrade_upgrade_and_constraints():
    configured = os.getenv("MAWOS_POSTGRES_TEST_URL")
    if not configured:
        pytest.skip("Set MAWOS_POSTGRES_TEST_URL to the isolated mawos_test database")
    url = make_url(configured)
    require_test_database_name(url.database)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("Use Psycopg 3 for parent PostgreSQL tests")
    engine = create_engine(url, pool_pre_ping=True)
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260914_parent_portal.py"
    spec = importlib.util.spec_from_file_location("parent_portal_migration", path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    try:
        with engine.connect() as connection:
            require_test_database_name(connection.execute(text("SELECT current_database()")).scalar_one())
            connection.rollback(); transaction = connection.begin()
            schema = "parent_migration_" + uuid.uuid4().hex
            connection.execute(text(f"CREATE SCHEMA {schema}"))
            connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            connection.execute(text("""CREATE TABLE students (
                usn varchar(16) PRIMARY KEY)"""))
            connection.execute(text("""CREATE TABLE users (
                id serial PRIMARY KEY, username varchar(64) NOT NULL UNIQUE,
                password_hash varchar(256) NOT NULL, role varchar(16) NOT NULL,
                display_name varchar(128) NOT NULL)"""))
            connection.execute(text("INSERT INTO students VALUES ('PG-PARENT-1')"))
            connection.execute(text("INSERT INTO users(username,password_hash,role,display_name) VALUES ('pg.parent','hash','parent','Parent')"))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
                assert connection.execute(text("SELECT must_change_password FROM users")).scalar_one() is False
                connection.execute(text("INSERT INTO parents(user_id,full_name) VALUES (1,'Parent')"))
                connection.execute(text("INSERT INTO parent_students(parent_id,student_usn,relationship) VALUES (1,'PG-PARENT-1','Guardian')"))
                with pytest.raises(IntegrityError):
                    with connection.begin_nested():
                        connection.execute(text("INSERT INTO parent_students(parent_id,student_usn,relationship) VALUES (1,'PG-PARENT-1','Guardian')"))
                migration.downgrade()
                migration.upgrade()
            assert connection.execute(text("SELECT count(*) FROM parents")).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM parent_students")).scalar_one() == 1
            indexes = {item["name"] for item in inspect(connection).get_indexes("parent_students")}
            assert "uq_parent_students_active" in indexes
            transaction.rollback()
    finally:
        engine.dispose()
