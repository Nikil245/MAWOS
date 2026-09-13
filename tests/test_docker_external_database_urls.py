"""Compose must keep native and container PostgreSQL URLs distinct."""
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
NATIVE_URL = "postgresql+psycopg://mawos_app:test-password@127.0.0.1:5432/mawos"
DOCKER_URL = "postgresql+psycopg://mawos_app:test-password@host.docker.internal:5432/mawos"


def _compose(env):
    return subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config"], cwd=ROOT, env=env,
        text=True, capture_output=True, check=False,
    )


def _external_environment():
    return {
        **os.environ,
        "MAWOS_DATABASE_MODE": "external",
        "MAWOS_DATABASE_URL": NATIVE_URL,
        "MAWOS_DOCKER_DATABASE_URL": DOCKER_URL,
        "MAWOS_JWT_SECRET": "test-only-secret",
    }


def test_external_compose_uses_docker_url_inside_backend_only():
    result = _compose(_external_environment())
    assert result.returncode == 0, result.stderr
    assert f"MAWOS_DATABASE_URL: {DOCKER_URL}" in result.stdout
    assert NATIVE_URL not in result.stdout
    assert "host.docker.internal=host-gateway" in result.stdout
    assert "postgres:" not in result.stdout


def test_external_compose_fails_clearly_without_docker_url():
    env = _external_environment()
    env.pop("MAWOS_DOCKER_DATABASE_URL")
    result = _compose(env)
    assert result.returncode != 0
    assert "MAWOS_DOCKER_DATABASE_URL" in result.stderr


def test_native_url_remains_loopback_for_alembic_environment():
    # Alembic reads MAWOS_DATABASE_URL directly; Compose does not rewrite the
    # parent shell's native variable.
    env = _external_environment()
    assert env["MAWOS_DATABASE_URL"].split("@")[1].startswith("127.0.0.1:")
