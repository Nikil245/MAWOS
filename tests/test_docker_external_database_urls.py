"""Compose must keep native and container PostgreSQL URLs distinct."""
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
NATIVE_URL = "postgresql+psycopg://mawos_app:test-password@127.0.0.1:5432/mawos"
DOCKER_URL = "postgresql+psycopg://mawos_app:test-password@host.docker.internal:5432/mawos"


def _compose(env, *config_args):
    return subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config", *config_args],
        cwd=ROOT, env=env,
        text=True, capture_output=True, check=False,
    )


def _docker_database_compose(env, *config_args):
    return subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "-f", "docker-compose.yml",
         "-f", "docker-compose.docker-db.yml", "config", *config_args],
        cwd=ROOT, env={**env, "POSTGRES_DB": "mawos", "POSTGRES_USER": "mawos_app",
                       "POSTGRES_PASSWORD": "test-password"},
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


def test_ollama_is_a_default_healthy_backend_dependency():
    services = _compose(_external_environment(), "--services")
    assert services.returncode == 0, services.stderr
    assert {"backend", "frontend", "ollama"} <= set(services.stdout.splitlines())

    rendered = _compose(_external_environment(), "--format", "json")
    assert rendered.returncode == 0, rendered.stderr
    config = json.loads(rendered.stdout)
    ollama = config["services"]["ollama"]
    assert not ollama.get("profiles")
    assert ollama["volumes"][0]["source"] == "ollama_data"
    assert ollama["healthcheck"]["test"] == ["CMD", "ollama", "list"]
    assert config["services"]["backend"]["depends_on"]["ollama"]["condition"] == "service_healthy"
    assert config["services"]["backend"]["environment"]["MAWOS_OLLAMA_HOST"] == "http://ollama:11434"


def test_docker_ollama_has_no_database_network_or_credentials():
    rendered = _docker_database_compose(_external_environment(), "--format", "json")
    assert rendered.returncode == 0, rendered.stderr
    config = json.loads(rendered.stdout)
    services = config["services"]
    ollama_networks = set(services["ollama"]["networks"])
    postgres_networks = set(services["postgres"]["networks"])
    backend_networks = set(services["backend"]["networks"])
    assert ollama_networks.isdisjoint(postgres_networks)
    assert backend_networks & ollama_networks
    assert backend_networks & postgres_networks
    assert not any("DATABASE" in key or "POSTGRES" in key
                   for key in services["ollama"].get("environment", {}))
