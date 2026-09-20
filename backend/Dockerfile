# Build from the repository root: docker build -f backend/Dockerfile .
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

RUN groupadd --system mawos && useradd --system --gid mawos --create-home mawos
WORKDIR /app
RUN mkdir -p /var/lib/mawos/placement-documents \
    && chown -R mawos:mawos /var/lib/mawos

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip check

# The root .dockerignore excludes local environments, data, tests,
# credentials, backups, and generated artifacts from the build context.
COPY --chown=mawos:mawos backend ./backend
COPY --chown=mawos:mawos alembic ./alembic
COPY --chown=mawos:mawos alembic.ini ./
COPY --chown=mawos:mawos ml/models ./ml/models
RUN chmod 0555 /app/backend/docker-entrypoint.sh

USER mawos
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT', '8000') + '/', timeout=3)" || exit 1

# Run the reviewed migration chain before FastAPI starts. Keeping this in the
# image means platforms such as Render can leave their Docker Command blank.
CMD ["/app/backend/docker-entrypoint.sh"]
