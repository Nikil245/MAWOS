"""Non-connecting safety checks for MAWOS PostgreSQL operations."""
from sqlalchemy.engine import URL, make_url


class PostgreSQLSafetyError(RuntimeError):
    """Raised before an operation could target an unsafe database."""


def isolated_test_database_url(live_url: str | None, test_url: str | None) -> URL:
    """Validate the fixed live/test database pairing without connecting.

    The returned URL is deliberately the *test* URL.  Callers must construct
    their integration engine only from this value, never from application
    configuration.
    """
    if not live_url or not live_url.strip():
        raise PostgreSQLSafetyError("MAWOS_DATABASE_URL must be configured")
    if not test_url or not test_url.strip():
        raise PostgreSQLSafetyError("MAWOS_POSTGRES_TEST_URL must be configured")
    try:
        live = make_url(live_url)
        test = make_url(test_url)
    except Exception as exc:
        raise PostgreSQLSafetyError("PostgreSQL URL is invalid") from exc

    if live.drivername != "postgresql+psycopg" or live.database != "mawos":
        raise PostgreSQLSafetyError("MAWOS_DATABASE_URL must target PostgreSQL database mawos")
    if test.drivername != "postgresql+psycopg" or test.database != "mawos_test":
        raise PostgreSQLSafetyError(
            "MAWOS_POSTGRES_TEST_URL must target PostgreSQL database mawos_test"
        )
    if test == live:
        raise PostgreSQLSafetyError("test database URL must differ from the live URL")
    return test


def require_test_database_name(database_name: str) -> None:
    """Permit destructive test cleanup only after server-side target checking."""
    if database_name != "mawos_test":
        raise PostgreSQLSafetyError("destructive test cleanup requires database mawos_test")
