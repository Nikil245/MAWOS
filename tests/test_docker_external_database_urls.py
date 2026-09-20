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


def test_ollama_is_optional_and_backend_accepts_hosted_provider_configuration():
    services = _compose(_external_environment(), "--services")
    assert services.returncode == 0, services.stderr
    assert set(services.stdout.splitlines()) == {"backend", "frontend"}

    env = {**_external_environment(), "COMPOSE_PROFILES": "local-ai"}
    rendered = _compose(env, "--format", "json")
    assert rendered.returncode == 0, rendered.stderr
    config = json.loads(rendered.stdout)
    ollama = config["services"]["ollama"]
    assert ollama["profiles"] == ["local-ai"]
    assert ollama["volumes"][0]["source"] == "ollama_data"
    assert ollama["healthcheck"]["test"] == ["CMD", "ollama", "list"]
    assert "ollama" not in config["services"]["backend"].get("depends_on", {})
    assert config["services"]["backend"]["environment"]["MAWOS_AI_PROVIDER"] == "auto"
    assert config["services"]["backend"]["environment"]["MAWOS_GROQ_MODEL"] == "openai/gpt-oss-20b"
    assert config["services"]["backend"]["environment"]["MAWOS_OLLAMA_HOST"] == "http://ollama:11434"
    assert config["services"]["backend"]["environment"]["MAWOS_OLLAMA_TIMEOUT_SECONDS"] == "90"
    assert config["services"]["backend"]["environment"]["MAWOS_OLLAMA_KEEP_ALIVE_SECONDS"] == "300"


def test_docker_ollama_has_no_database_network_or_credentials():
    env = {**_external_environment(), "COMPOSE_PROFILES": "local-ai"}
    rendered = _docker_database_compose(env, "--format", "json")
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


def test_groq_key_is_forwarded_only_to_backend_runtime():
    marker = "compose-test-key-not-real"
    env = {**_external_environment(), "GROQ_API_KEY": marker}
    rendered = _compose(env, "--format", "json")
    assert rendered.returncode == 0, rendered.stderr
    config = json.loads(rendered.stdout)
    assert config["services"]["backend"]["environment"]["GROQ_API_KEY"] == marker
    assert marker not in json.dumps(config["services"]["frontend"])
    assert "GROQ_API_KEY" not in config["services"]["frontend"].get("build", {}).get("args", {})


def test_runtime_ports_keep_local_defaults_and_allow_render_overrides():
    rendered = _compose(_external_environment(), "--format", "json")
    assert rendered.returncode == 0, rendered.stderr
    config = json.loads(rendered.stdout)
    assert config["services"]["backend"]["ports"] == [{"mode": "ingress", "target": 8000, "published": "8000", "protocol": "tcp"}]
    assert config["services"]["frontend"]["ports"] == [{"mode": "ingress", "target": 8080, "published": "3000", "protocol": "tcp"}]
    assert 'PORT=8000' in (ROOT / "backend/Dockerfile").read_text()
    entrypoint = (ROOT / "backend/docker-entrypoint.sh").read_text()
    assert entrypoint.startswith("#!/bin/sh\nset -eu\n")
    assert "alembic upgrade head" in entrypoint
    assert 'exec uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}"' in entrypoint
    assert 'CMD ["/app/backend/docker-entrypoint.sh"]' in (ROOT / "backend/Dockerfile").read_text()
    assert 'ENV PORT=8080' in (ROOT / "frontend/Dockerfile").read_text()
    assert '${PORT}' in (ROOT / "frontend/nginx.conf").read_text()
