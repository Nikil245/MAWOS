#!/bin/sh
set -eu

mode="${MAWOS_ENV:-}"
case "$mode" in
  development|test|production) ;;
  *)
    echo "MAWOS_ENV must be explicitly configured as development, test, or production" >&2
    exit 64
    ;;
esac

migration_mode="${MAWOS_RUN_MIGRATIONS:-auto}"
case "$migration_mode" in
  true|false|auto) ;;
  *)
    echo "MAWOS_RUN_MIGRATIONS must be auto, true, or false" >&2
    exit 64
    ;;
esac

if [ "$migration_mode" = "auto" ]; then
  if [ "$mode" = "production" ]; then
    migration_mode=true
  else
    migration_mode=false
  fi
fi

if [ "$migration_mode" = "true" ]; then
  if [ -z "${MAWOS_MIGRATION_DATABASE_URL:-}" ]; then
    echo "MAWOS_MIGRATION_DATABASE_URL is required when startup migrations are enabled" >&2
    exit 64
  fi
  alembic upgrade head
fi

# A migration-owner URL is required only for Alembic and must not remain in
# the FastAPI runtime process environment.
unset MAWOS_MIGRATION_DATABASE_URL
exec uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
