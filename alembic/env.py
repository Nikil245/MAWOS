"""Alembic configuration for the already-existing MAWOS PostgreSQL schema."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool

from backend.app import config as app_config
from backend.app.database import Base
import backend.app.models  # Register model metadata.


alembic_config = context.config
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    app_config.validate_database_configuration()
    url = app_config.migration_database_url()
    if make_url(url).get_backend_name() != "postgresql":
        raise RuntimeError("Alembic requires a PostgreSQL migration URL")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema="public",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = alembic_config.get_section(alembic_config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema="public",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
