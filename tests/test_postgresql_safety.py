"""Regression tests for PostgreSQL live/test isolation without connections."""
import pytest

from backend.app.postgresql_safety import (
    PostgreSQLSafetyError,
    isolated_test_database_url,
    require_test_database_name,
)


LIVE = "postgresql+psycopg://live@127.0.0.1:5432/mawos"
TEST = "postgresql+psycopg://test@127.0.0.1:5432/mawos_test"


def test_isolated_test_url_accepts_only_the_expected_pair():
    assert isolated_test_database_url(LIVE, TEST).database == "mawos_test"


@pytest.mark.parametrize("live_url,test_url", [
    (None, TEST),
    ("sqlite:///mawos.db", TEST),
    (LIVE.replace("/mawos", "/other"), TEST),
    (LIVE, "sqlite:///mawos_test.db"),
    (LIVE, TEST.replace("/mawos_test", "/mawos")),
])
def test_isolated_test_url_rejects_unsafe_targets(live_url, test_url):
    with pytest.raises(PostgreSQLSafetyError):
        isolated_test_database_url(live_url, test_url)


def test_cleanup_guard_requires_test_database():
    require_test_database_name("mawos_test")
    with pytest.raises(PostgreSQLSafetyError):
        require_test_database_name("mawos")
