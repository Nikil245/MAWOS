"""Strict browser-origin policy tests."""
import pytest
from fastapi.testclient import TestClient

from backend.app import config
from backend.app.auth import create_token
from backend.app.main import app
from backend.app.models import User


ORIGIN = "http://localhost:3000"


def test_allowed_options_preflight():
    response = TestClient(app).options("/api/me", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers


def test_allowed_authenticated_request_has_origin_header(db):
    user = User(username="cors.student", password_hash="not-used", role="student",
                display_name="CORS Student")
    db.add(user); db.commit()
    response = TestClient(app).get("/api/me", headers={
        "Origin": ORIGIN, "Authorization": f"Bearer {create_token(user)}",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_unknown_origin_is_not_allowed():
    response = TestClient(app).options("/api/me", headers={
        "Origin": "https://untrusted.example",
        "Access-Control-Request-Method": "GET",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("value", ["*", "https://example.test/path", "ftp://example.test"])
def test_invalid_cors_origin_is_rejected(monkeypatch, value):
    monkeypatch.setenv("MAWOS_ENV", "production")
    monkeypatch.setenv("MAWOS_CORS_ORIGINS", value)
    with pytest.raises(config.ConfigurationError):
        config.cors_origins()


def test_production_requires_explicit_cors_origins(monkeypatch):
    monkeypatch.setenv("MAWOS_ENV", "production")
    monkeypatch.delenv("MAWOS_CORS_ORIGINS", raising=False)
    with pytest.raises(config.ConfigurationError, match="MAWOS_CORS_ORIGINS"):
        config.cors_origins()
