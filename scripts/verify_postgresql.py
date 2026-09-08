"""Read-only PostgreSQL verification for the existing MAWOS schema.

This script never imports FastAPI startup, runs seed logic, or applies DDL.
Run it only after loading an ignored local .env file.
"""
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import config
from backend.app.database import Base
import backend.app.models  # Register all ORM tables on Base.metadata.


POSTGRESQL_TABLES = sorted(Base.metadata.tables)
POSTGRESQL_DIALECT = postgresql.dialect()
# Alembic's version table is infrastructure, not an application model table.
# It is the sole permitted additional table after baseline stamping.
ALLOWED_INFRASTRUCTURE_TABLES = frozenset({"alembic_version"})


def _normalized_type(type_) -> str:
    value = type_.compile(dialect=POSTGRESQL_DIALECT).lower().replace(" ", "")
    return {"float": "doubleprecision", "float(53)": "doubleprecision"}.get(value, value)


def _sequence_from_default(value: str | None) -> str | None:
    match = re.fullmatch(r"nextval\('(?:\"public\"\.)?([^']+)'::regclass\)", value or "")
    return match.group(1) if match else None


def _normalized_server_default(value) -> str | None:
    """Compare PostgreSQL literal defaults without hiding expression changes.

    PostgreSQL's inspector adds casts and quotes to literal defaults, whereas
    SQLAlchemy stores the migration's literal.  Normalize only those spelling
    differences; expressions such as ``now()`` remain expressions.
    """
    if value is None:
        return None
    normalized = str(value).strip()
    normalized = re.sub(
        r"(?:::[A-Za-z_][A-Za-z_0-9]*(?:\s+[A-Za-z_][A-Za-z_0-9]*)*)+$",
        "",
        normalized,
    ).strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == "'":
        normalized = normalized[1:-1].replace("''", "'")
    if normalized.lower() in {"true", "false"}:
        return normalized.lower()
    return normalized


def _model_server_default(column) -> str | None:
    if column.server_default is None:
        return None
    return _normalized_server_default(column.server_default.arg)


def _server_default_difference(column, live_default) -> bool:
    return _model_server_default(column) != _normalized_server_default(live_default)


def _model_foreign_keys(table) -> list[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return sorted(
        (tuple(element.parent.name for element in constraint.elements),
         constraint.elements[0].column.table.name,
         tuple(element.column.name for element in constraint.elements))
        for constraint in table.foreign_key_constraints
    )


def _compare_table(inspector, table_name: str, sequences: set[str]) -> list[str]:
    model = Base.metadata.tables[table_name]
    differences: list[str] = []
    model_columns = {column.name: column for column in model.columns}
    live_columns = {
        column["name"]: column
        for column in inspector.get_columns(table_name, schema="public")
    }
    if set(model_columns) != set(live_columns):
        differences.append("column names")

    model_pk = tuple(model.primary_key.columns.keys())
    for column_name in sorted(set(model_columns) & set(live_columns)):
        model_column = model_columns[column_name]
        live_column = live_columns[column_name]
        if _normalized_type(model_column.type) != _normalized_type(live_column["type"]):
            differences.append(f"{column_name} type")
        if model_column.nullable != live_column["nullable"]:
            differences.append(f"{column_name} nullability")
        if column_name in model_pk and _normalized_type(model_column.type) == "integer":
            expected_sequence = f"{table_name}_{column_name}_seq"
            if _sequence_from_default(live_column.get("default")) != expected_sequence:
                differences.append(f"{column_name} sequence default")
            if expected_sequence not in sequences:
                differences.append(f"{column_name} sequence")
        elif _server_default_difference(model_column, live_column.get("default")):
            differences.append(f"{column_name} server default")

    live_pk = tuple(inspector.get_pk_constraint(table_name, schema="public").get("constrained_columns") or ())
    if model_pk != live_pk:
        differences.append("primary key")

    model_unique = sorted(
        tuple(constraint.columns.keys()) for constraint in model.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    )
    live_unique = sorted(
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table_name, schema="public")
    )
    if model_unique != live_unique:
        differences.append("unique constraints")

    live_foreign_keys = sorted(
        (tuple(foreign_key["constrained_columns"]), foreign_key["referred_table"],
         tuple(foreign_key["referred_columns"]))
        for foreign_key in inspector.get_foreign_keys(table_name, schema="public")
    )
    if _model_foreign_keys(model) != live_foreign_keys:
        differences.append("foreign keys")

    model_indexes = sorted(
        (index.name, tuple(index.columns.keys()), index.unique) for index in model.indexes
    )
    live_indexes = sorted(
        (index["name"], tuple(index["column_names"]), bool(index["unique"]))
        for index in inspector.get_indexes(table_name, schema="public")
        if not index.get("duplicates_constraint")
    )
    if model_indexes != live_indexes:
        differences.append("indexes")
    return differences


def main() -> int:
    config.validate_database_configuration()
    if config.database_backend() != "postgresql":
        raise RuntimeError("MAWOS_DATABASE_URL must select PostgreSQL")

    engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("BEGIN TRANSACTION READ ONLY"))
            database = connection.execute(text("SELECT current_database()")).scalar_one()
            schema = connection.execute(text("SELECT current_schema()")).scalar_one()
            version = connection.execute(text("SHOW server_version")).scalar_one()
            if database != "mawos" or schema != "public":
                raise RuntimeError("Expected PostgreSQL database mawos and schema public")

            inspector = inspect(connection)
            live_tables = set(inspector.get_table_names(schema="public"))
            expected_tables = set(POSTGRESQL_TABLES)
            missing = sorted(expected_tables - live_tables)
            unexpected = sorted(
                live_tables - expected_tables - ALLOWED_INFRASTRUCTURE_TABLES
            )
            sequences = set(inspector.get_sequence_names(schema="public"))

            print(f"database: {database}")
            print(f"schema: {schema}")
            print(f"backend: {engine.dialect.name}")
            print(f"server_version: {version}")
            print("row counts:")
            for table_name in POSTGRESQL_TABLES:
                count = connection.execute(
                    text(f'SELECT count(*) FROM public."{table_name}"')
                ).scalar_one()
                print(f"  {table_name}: {count}")

            differences = {}
            for table_name in POSTGRESQL_TABLES:
                if table_name not in live_tables:
                    continue
                result = _compare_table(inspector, table_name, sequences)
                if result:
                    differences[table_name] = result
            connection.rollback()
    finally:
        engine.dispose()

    if missing or unexpected or differences:
        print(f"missing tables: {', '.join(missing) if missing else 'none'}")
        print(f"unexpected tables: {', '.join(unexpected) if unexpected else 'none'}")
        for table_name, result in differences.items():
            print(f"{table_name}: {', '.join(result)}")
        print("schema match result: FAIL")
        return 1

    print("schema match result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
