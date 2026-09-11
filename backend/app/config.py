"""Central configuration for MAWOS.

Everything is overridable via environment variables so the same codebase
runs on SQLite (default, zero-install) or PostgreSQL, and with or without
a local Ollama LLM.
"""
import os
from pathlib import Path

from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class ConfigurationError(RuntimeError):
    """Raised when a security-sensitive MAWOS setting is unsafe."""


ENVIRONMENTS = frozenset({"development", "test", "production"})
DATABASE_MODES = frozenset({"external", "docker"})
MIN_JWT_SECRET_BYTES = 32

# This fallback is deliberately limited to non-production modes. It is
# convenient for local development, but is public source code and unsafe for
# any deployed service.
INSECURE_DEVELOPMENT_JWT_SECRET = "mawos-development-only-insecure-secret-do-not-use-in-production"
KNOWN_INSECURE_JWT_SECRETS = frozenset({
    "mawos-dev-secret-change-in-prod",
    INSECURE_DEVELOPMENT_JWT_SECRET,
    "replace-with-a-long-random-secret",
})


def environment_mode() -> str:
    """Return the explicit MAWOS deployment mode."""
    raw_mode = os.getenv("MAWOS_ENV")
    if raw_mode is None or not raw_mode.strip():
        raise ConfigurationError(
            "MAWOS_ENV must be explicitly configured as development, test, or production"
        )
    mode = raw_mode.strip().lower()
    if mode not in ENVIRONMENTS:
        raise ConfigurationError(
            "MAWOS_ENV must be one of: development, test, production"
        )
    return mode


def _boolean_setting(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _positive_int_setting(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _positive_float_setting(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive number")
    return value


def _has_strong_secret_shape(secret: str) -> bool:
    """Reject obvious low-entropy values; operators must use random secrets."""
    return len(set(secret)) >= 16


def jwt_secret() -> str:
    """Return the configured signing secret, enforcing production safety."""
    mode = environment_mode()
    configured_secret = os.getenv("MAWOS_JWT_SECRET")

    if mode != "production" and not configured_secret:
        return INSECURE_DEVELOPMENT_JWT_SECRET

    if mode == "production" and not configured_secret:
        raise ConfigurationError(
            "MAWOS_JWT_SECRET must be explicitly configured in production"
        )

    assert configured_secret is not None
    if mode == "production":
        if configured_secret in KNOWN_INSECURE_JWT_SECRETS:
            raise ConfigurationError(
                "MAWOS_JWT_SECRET must not use a known development secret in production"
            )
        if len(configured_secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            raise ConfigurationError(
                "MAWOS_JWT_SECRET must contain at least 32 bytes in production"
            )
        if not _has_strong_secret_shape(configured_secret):
            raise ConfigurationError(
                "MAWOS_JWT_SECRET must be a high-entropy random value in production"
            )
    return configured_secret


def seed_demo_data_enabled() -> bool:
    """Return whether automatic demo-data seeding is allowed at startup."""
    enabled = _boolean_setting("MAWOS_SEED_DEMO_DATA", default=False)
    if environment_mode() == "production" and enabled:
        raise ConfigurationError(
            "MAWOS_SEED_DEMO_DATA is not allowed in production; "
            "production demo seeding requires a separate administrative operation"
        )
    return enabled


def validate_security_configuration() -> None:
    """Validate all security-sensitive startup settings without exposing secrets."""
    jwt_secret()
    seed_demo_data_enabled()


def database_url() -> str:
    """Return a validated database URL without ever exposing credentials."""
    configured_url = os.getenv("MAWOS_DATABASE_URL")
    if configured_url is None or not configured_url.strip():
        if environment_mode() == "production":
            raise ConfigurationError(
                "MAWOS_DATABASE_URL must be explicitly configured in production"
            )
        return f"sqlite:///{BASE_DIR / 'mawos.db'}"

    try:
        parsed = make_url(configured_url)
    except Exception as exc:
        raise ConfigurationError("MAWOS_DATABASE_URL is invalid") from exc

    if parsed.drivername == "sqlite":
        return configured_url
    if parsed.drivername != "postgresql+psycopg":
        raise ConfigurationError(
            "MAWOS_DATABASE_URL must use sqlite or postgresql+psycopg"
        )
    return configured_url


def database_backend() -> str:
    """Return only the safe backend name, never connection details."""
    return make_url(DATABASE_URL).get_backend_name()


def database_mode() -> str:
    """Return the explicitly selected database deployment target.

    This prevents a Compose deployment from accidentally treating a fresh
    local volume as the institution's existing PostgreSQL database.
    """
    raw_mode = os.getenv("MAWOS_DATABASE_MODE")
    if raw_mode is None or not raw_mode.strip():
        raise ConfigurationError(
            "MAWOS_DATABASE_MODE must be explicitly configured as external or docker"
        )
    mode = raw_mode.strip().lower()
    if mode not in DATABASE_MODES:
        raise ConfigurationError("MAWOS_DATABASE_MODE must be external or docker")
    return mode


def validate_database_configuration() -> None:
    """Validate the selected backend and PostgreSQL seed safeguards."""
    database_mode()
    backend = database_backend()
    if backend not in {"sqlite", "postgresql"}:
        raise ConfigurationError("MAWOS_DATABASE_URL selects an unsupported backend")
    if backend == "postgresql" and seed_demo_data_enabled():
        raise ConfigurationError(
            "MAWOS_SEED_DEMO_DATA is not allowed when PostgreSQL is active"
        )


# Shared Institutional Context Store.
# Default: SQLite file outside production. PostgreSQL must use Psycopg 3.
DATABASE_URL = database_url()

# JWT auth
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 12

# Local LLM (optional). The system is fully functional without it —
# the deterministic keyword classifier handles intent routing.
OLLAMA_HOST = os.getenv("MAWOS_OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
# Runtime default for the local deployment.  The frozen research model remains
# recorded in router_config.json and evaluation artifacts; it is not changed.
OLLAMA_MODEL = os.getenv("MAWOS_OLLAMA_MODEL", "qwen2.5:3b")
# One complete assistant-turn budget, including availability checks and every
# model round.  There is intentionally no hidden multiplier in the client.
OLLAMA_TIMEOUT_S = _positive_float_setting("MAWOS_OLLAMA_TIMEOUT", 20.0)
OLLAMA_CONTEXT_TOKENS = _positive_int_setting("MAWOS_OLLAMA_CONTEXT", 2048)
OLLAMA_MAX_OUTPUT_TOKENS = _positive_int_setting("MAWOS_OLLAMA_MAX_OUTPUT_TOKENS", 192)
OLLAMA_MAX_RESPONSE_CHARS = _positive_int_setting("MAWOS_OLLAMA_MAX_RESPONSE_CHARS", 12000)
OLLAMA_MAX_ROUNDS = _positive_int_setting("MAWOS_OLLAMA_MAX_ROUNDS", 2)
OLLAMA_CONCURRENCY = _positive_int_setting("MAWOS_OLLAMA_CONCURRENCY", 1)
OLLAMA_HEALTH_TTL_S = _positive_float_setting("MAWOS_OLLAMA_HEALTH_TTL", 5.0)
OLLAMA_RETRY_COOLDOWN_S = _positive_float_setting("MAWOS_OLLAMA_RETRY_COOLDOWN", 2.0)

# P3 — PCN-style provenance gate on the LLM tier's free-text answers
# (backend/app/provenance.py, docs/RESEARCH_PLAN_V3.md §3.2). On by
# default; the dev-only evaluation is evaluation/gate_p3.py.
PROVENANCE_GATE_ENABLED = os.getenv("MAWOS_PROVENANCE_GATE", "1") == "1"

# Institutional business rules
ATTENDANCE_THRESHOLD = 75.0          # % required for hall ticket
ABSENCE_STREAK_ALERT = 3             # consecutive absences that trigger an alert
FEE_LATE_FINE_PER_DAY = 50.0         # Rs per day after grace period
FEE_GRACE_DAYS = 7
LIBRARY_LOAN_DAYS = 14
LIBRARY_FINE_PER_DAY = 5.0           # Rs per day overdue

# ML model artifacts
ML_MODELS_DIR = BASE_DIR / "ml" / "models"
ML_DATA_DIR = BASE_DIR / "ml" / "data"
