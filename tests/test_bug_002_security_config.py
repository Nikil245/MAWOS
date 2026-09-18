"""BUG-002: JWT configuration and automatic demo-data seeding safeguards."""
from unittest.mock import Mock

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import config
from backend.app.auth import create_token
from backend.app.database import Base
from backend.app.main import app
from backend.app.models import User


PRODUCTION_SECRET = "test-production-secret-0123456789-abcdefghijklmnopqrstuvwxyz"


def _set_production(monkeypatch, secret: str | None) -> None:
    monkeypatch.setenv("MAWOS_ENV", "production")
    monkeypatch.setenv("MAWOS_SEED_DEMO_DATA", "false")
    monkeypatch.setenv("MAWOS_CORS_ORIGINS", "https://frontend.example.test")
    if secret is None:
        monkeypatch.delenv("MAWOS_JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("MAWOS_JWT_SECRET", secret)


def test_environment_mode_must_be_explicit(monkeypatch):
    monkeypatch.delenv("MAWOS_ENV", raising=False)

    with pytest.raises(config.ConfigurationError, match="MAWOS_ENV"):
        config.validate_security_configuration()


def test_production_startup_fails_without_jwt_secret(monkeypatch):
    _set_production(monkeypatch, None)

    with pytest.raises(config.ConfigurationError, match="MAWOS_JWT_SECRET"):
        with TestClient(app):
            pass


@pytest.mark.parametrize("secret", [
    "too-short-secret",
    "mawos-dev-secret-change-in-prod",
    config.INSECURE_DEVELOPMENT_JWT_SECRET,
    "a" * 32,
])
def test_production_rejects_unsafe_jwt_secrets(monkeypatch, secret):
    _set_production(monkeypatch, secret)

    with pytest.raises(config.ConfigurationError, match="MAWOS_JWT_SECRET"):
        config.validate_security_configuration()


def test_production_accepts_strong_configured_jwt_secret(monkeypatch):
    _set_production(monkeypatch, PRODUCTION_SECRET)

    config.validate_security_configuration()
    assert config.jwt_secret() == PRODUCTION_SECRET
    with TestClient(app) as client:
        assert client.get("/").status_code == 200


def test_production_startup_never_calls_demo_seed(monkeypatch):
    _set_production(monkeypatch, PRODUCTION_SECRET)
    seed_all = Mock()
    monkeypatch.setattr("backend.app.main.seed_all", seed_all)

    with TestClient(app) as client:
        assert client.get("/").status_code == 200

    seed_all.assert_not_called()


def test_development_seeding_is_disabled_by_default_and_when_false(monkeypatch):
    monkeypatch.setenv("MAWOS_ENV", "development")
    monkeypatch.delenv("MAWOS_SEED_DEMO_DATA", raising=False)
    assert config.seed_demo_data_enabled() is False

    monkeypatch.setenv("MAWOS_SEED_DEMO_DATA", "false")
    assert config.seed_demo_data_enabled() is False


def test_development_seed_runs_only_when_explicitly_enabled_and_is_idempotent(monkeypatch):
    monkeypatch.setenv("MAWOS_ENV", "development")
    monkeypatch.setenv("MAWOS_SEED_DEMO_DATA", "true")
    assert config.seed_demo_data_enabled() is True

    from backend.app import main

    results = iter([True, False])
    seed_all = Mock(side_effect=lambda: next(results))
    bootstrap = Mock()
    monkeypatch.setattr(main, "seed_all", seed_all)
    monkeypatch.setattr(main, "bootstrap_evaluations", bootstrap)

    with TestClient(app):
        pass
    with TestClient(app):
        pass

    assert seed_all.call_count == 2
    bootstrap.assert_called_once()


def test_explicit_development_seed_data_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("MAWOS_ENV", "development")
    monkeypatch.setenv("MAWOS_SEED_DEMO_DATA", "true")
    assert config.seed_demo_data_enabled() is True

    from backend.app import seed

    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                                   future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(seed, "SessionLocal", session_factory)

    assert seed.seed_all(per_section=1) is True
    assert seed.seed_all(per_section=1) is False

    session = session_factory()
    try:
        roles = {role for (role,) in session.query(User.role).distinct()}
        assert {"student", "faculty", "hod", "principal", "admin"} <= roles
    finally:
        session.close()
        engine.dispose()


def test_seed_does_not_change_an_existing_database(monkeypatch, tmp_path):
    from backend.app import seed

    engine = create_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                                   future=True)
    Base.metadata.create_all(bind=engine)
    session = session_factory()
    session.add(User(username="existing.admin", password_hash="existing-hash",
                     role="admin", display_name="Existing Admin"))
    session.commit()
    session.close()
    monkeypatch.setattr(seed, "SessionLocal", session_factory)

    assert seed.seed_all(per_section=1) is False

    session = session_factory()
    try:
        user = session.query(User).filter_by(username="existing.admin").one()
        assert user.password_hash == "existing-hash"
        assert session.query(User).count() == 1
    finally:
        session.close()
        engine.dispose()


def test_test_mode_uses_a_deterministic_test_secret(monkeypatch):
    test_secret = "deterministic-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setenv("MAWOS_ENV", "test")
    monkeypatch.setenv("MAWOS_JWT_SECRET", test_secret)
    monkeypatch.setenv("MAWOS_SEED_DEMO_DATA", "false")

    config.validate_security_configuration()
    assert config.jwt_secret() == test_secret
    assert config.seed_demo_data_enabled() is False


def test_jwt_payload_and_verification_remain_compatible(monkeypatch):
    test_secret = "deterministic-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setenv("MAWOS_ENV", "test")
    monkeypatch.setenv("MAWOS_JWT_SECRET", test_secret)
    user = User(username="token.user", password_hash="unused", role="faculty",
                display_name="Token User", usn=None, dept_code="AIML")

    token = create_token(user)
    payload = jwt.decode(token, test_secret, algorithms=[config.JWT_ALGORITHM])

    assert payload["sub"] == "token.user"
    assert payload["role"] == "faculty"
    assert payload["name"] == "Token User"
    assert "usn" in payload
    assert "exp" in payload


def test_secret_is_not_in_configuration_errors_or_logs(monkeypatch, caplog):
    secret = "do-not-disclose-this-short-secret"
    _set_production(monkeypatch, secret)

    with pytest.raises(config.ConfigurationError) as error:
        config.validate_security_configuration()

    assert secret not in str(error.value)
    assert secret not in caplog.text
