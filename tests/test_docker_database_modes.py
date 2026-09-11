"""Database-target selection is explicit before application startup."""
import pytest

from backend.app import config


@pytest.mark.parametrize("mode", ["external", "docker"])
def test_database_mode_accepts_the_two_documented_targets(monkeypatch, mode):
    monkeypatch.setenv("MAWOS_DATABASE_MODE", mode)
    assert config.database_mode() == mode


def test_database_mode_rejects_an_implicit_target(monkeypatch):
    monkeypatch.delenv("MAWOS_DATABASE_MODE", raising=False)
    with pytest.raises(config.ConfigurationError, match="explicitly configured"):
        config.database_mode()


def test_database_mode_rejects_unknown_target(monkeypatch):
    monkeypatch.setenv("MAWOS_DATABASE_MODE", "automatic")
    with pytest.raises(config.ConfigurationError, match="external or docker"):
        config.database_mode()
