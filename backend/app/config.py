"""Central configuration for MAWOS.

Everything is overridable via environment variables so the same codebase
runs on SQLite (default, zero-install) or PostgreSQL, and with or without
a local Ollama LLM.
"""
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class ConfigurationError(RuntimeError):
    """Raised when a security-sensitive MAWOS setting is unsafe."""


ENVIRONMENTS = frozenset({"development", "test", "production"})
DATABASE_MODES = frozenset({"external", "docker"})
AI_PROVIDERS = frozenset({"auto", "groq", "ollama", "disabled"})
MIN_JWT_SECRET_BYTES = 32
DEVELOPMENT_CORS_ORIGINS = (
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:5173", "http://127.0.0.1:5173",
)

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
    if not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{name} must be a positive number")
    return value


def _bounded_positive_float_setting(name: str, default: float,
                                    maximum: float) -> float:
    value = _positive_float_setting(name, default)
    if value > maximum:
        raise ConfigurationError(f"{name} must be at most {maximum:g}")
    return value


def _bounded_positive_int_setting(name: str, default: int, maximum: int) -> int:
    value = _positive_int_setting(name, default)
    if value > maximum:
        raise ConfigurationError(f"{name} must be at most {maximum}")
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
    cors_origins()


def cors_origins() -> tuple[str, ...]:
    """Return strict browser origins allowed to call the API.

    Bearer authentication does not use browser cookies, so CORS credentials
    remain disabled.  Wildcards are deliberately unsupported in every mode.
    """
    raw = os.getenv("MAWOS_CORS_ORIGINS", "").strip()
    if not raw:
        if environment_mode() == "production":
            raise ConfigurationError(
                "MAWOS_CORS_ORIGINS must list explicit HTTPS frontend origins in production"
            )
        return DEVELOPMENT_CORS_ORIGINS
    origins = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    if not origins:
        raise ConfigurationError("MAWOS_CORS_ORIGINS must contain at least one origin")
    if len(origins) != len(set(origins)):
        raise ConfigurationError("MAWOS_CORS_ORIGINS must not contain duplicate origins")
    for origin in origins:
        parsed = urlsplit(origin)
        if (origin == "*" or parsed.scheme not in {"http", "https"}
                or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            raise ConfigurationError(
                "MAWOS_CORS_ORIGINS entries must be origins such as https://frontend.example"
            )
        if environment_mode() == "production" and parsed.scheme != "https":
            raise ConfigurationError("MAWOS_CORS_ORIGINS must use HTTPS in production")
    return origins


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

# Optional generative provider. Deterministic MAWOS routes never consult this
# setting and remain available without a key, network, or model process.
AI_PROVIDER = os.getenv("MAWOS_AI_PROVIDER", "auto").strip().lower()
if AI_PROVIDER not in AI_PROVIDERS:
    raise ConfigurationError("MAWOS_AI_PROVIDER must be auto, groq, ollama, or disabled")
GROQ_BASE_URL = os.getenv(
    "MAWOS_GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
GROQ_MODEL = os.getenv("MAWOS_GROQ_MODEL", "openai/gpt-oss-20b").strip()
_groq_url = urlsplit(GROQ_BASE_URL)
if (_groq_url.scheme != "https" or not _groq_url.hostname or _groq_url.username
        or _groq_url.password or _groq_url.query or _groq_url.fragment):
    raise ConfigurationError(
        "MAWOS_GROQ_BASE_URL must be an HTTPS URL without credentials, query, or fragment")
if not GROQ_MODEL:
    raise ConfigurationError("MAWOS_GROQ_MODEL must not be empty")
GROQ_TIMEOUT_S = _bounded_positive_float_setting(
    "MAWOS_GROQ_TIMEOUT_SECONDS", 30.0, 60.0)
GROQ_MAX_TOKENS = min(
    _positive_int_setting("MAWOS_GROQ_MAX_TOKENS", 180), 180)
GROQ_MAX_INPUT_CHARS = min(
    _positive_int_setting("MAWOS_GROQ_MAX_INPUT_CHARS", 6000), 12000)
GROQ_HEALTH_TTL_S = _positive_float_setting("MAWOS_GROQ_HEALTH_TTL", 30.0)
GROQ_RETRY_COOLDOWN_S = _positive_float_setting("MAWOS_GROQ_RETRY_COOLDOWN", 5.0)
AI_GENERATIVE_REQUESTS_PER_MINUTE = min(
    _positive_int_setting("MAWOS_AI_REQUESTS_PER_MINUTE", 6), 60)

# Local LLM (optional). The system is fully functional without it —
# the deterministic keyword classifier handles intent routing.
OLLAMA_HOST = os.getenv("MAWOS_OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
# Runtime default for the local deployment.  The frozen research model remains
# recorded in router_config.json and evaluation artifacts; it is not changed.
OLLAMA_MODEL = os.getenv("MAWOS_OLLAMA_MODEL", "qwen2.5:3b")
# One complete assistant-turn budget, including availability checks and every
# model round.  There is intentionally no hidden multiplier in the client.
_OLLAMA_TIMEOUT_SETTING = (
    "MAWOS_OLLAMA_TIMEOUT_SECONDS"
    if os.getenv("MAWOS_OLLAMA_TIMEOUT_SECONDS", "").strip()
    else "MAWOS_OLLAMA_TIMEOUT"
)
# Honor the original MAWOS_OLLAMA_TIMEOUT name when the clearer new name is
# absent, so existing native deployments keep their configured deadline.
OLLAMA_TIMEOUT_S = _bounded_positive_float_setting(
    _OLLAMA_TIMEOUT_SETTING, 90.0, 600.0)
OLLAMA_CONTEXT_TOKENS = _positive_int_setting("MAWOS_OLLAMA_CONTEXT", 2048)
# Preserve compatibility with older local .env files while enforcing the
# response cap even if they still request the former 192-token value.
OLLAMA_MAX_OUTPUT_TOKENS = min(
    _positive_int_setting("MAWOS_OLLAMA_MAX_OUTPUT_TOKENS", 160), 180)
# Attach an explicit Ollama keep-alive to normal chat requests. This keeps the
# model resident without polling or background traffic.
OLLAMA_KEEP_ALIVE_S = _bounded_positive_int_setting(
    "MAWOS_OLLAMA_KEEP_ALIVE_SECONDS", 300, 86400)
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
# Monetary policy values remain Decimal throughout the library workflow.
from decimal import Decimal, InvalidOperation


def _library_money(name: str, default: str) -> Decimal:
    try:
        value = Decimal(os.getenv(name, default))
        if not value.is_finite() or value < 0 or value > Decimal("1000000") or value != value.quantize(Decimal("0.01")):
            raise ValueError()
        return value
    except (InvalidOperation, ValueError):
        raise ConfigurationError(f"{name} must be nonnegative money with at most two decimal places") from None


LIBRARY_LOAN_DAYS = _positive_int_setting("MAWOS_LIBRARY_LOAN_DAYS", 7)
LIBRARY_PICKUP_DEADLINE_DAYS = _positive_int_setting("MAWOS_LIBRARY_PICKUP_DEADLINE_DAYS", 2)
LIBRARY_FINE_PER_OVERDUE_DAY = _library_money("MAWOS_LIBRARY_FINE_PER_OVERDUE_DAY", "1")
LIBRARY_MISSED_PICKUP_FINE = _library_money("MAWOS_LIBRARY_MISSED_PICKUP_FINE", "10")
LIBRARY_FINE_BLOCK_THRESHOLD = _library_money("MAWOS_LIBRARY_FINE_BLOCK_THRESHOLD", "25")
LIBRARY_RECOMMENDATION_LIMIT = min(_positive_int_setting("MAWOS_LIBRARY_RECOMMENDATION_LIMIT", 5), 50)
LIBRARY_FINE_PER_DAY = LIBRARY_FINE_PER_OVERDUE_DAY

# ML model artifacts
ML_MODELS_DIR = BASE_DIR / "ml" / "models"
ML_DATA_DIR = BASE_DIR / "ml" / "data"

# Placement documents are private application data. They are served only by
# authenticated API handlers and must never live under frontend/static roots.
PLACEMENT_DOCUMENT_ROOT = Path(
    os.getenv("MAWOS_PLACEMENT_DOCUMENT_ROOT", str(BASE_DIR / "runtime" / "placement-documents"))
).expanduser().resolve()
PLACEMENT_DOCUMENT_MAX_BYTES = _positive_int_setting(
    "MAWOS_PLACEMENT_DOCUMENT_MAX_BYTES", 10 * 1024 * 1024
)
