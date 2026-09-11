# Docker deployment

## Prerequisites and setup

Install Docker Engine with the Compose plugin. Copy the example environment
file and replace every placeholder with target-appropriate values; `.env` is
ignored and must never be committed.

```bash
cp .env.example .env
# Edit .env: select MAWOS_DATABASE_MODE and set a production JWT secret.
```

`VITE_API_BASE_URL` is embedded while the frontend image is built. Its default
is `/api`, which keeps browser traffic same-origin: Nginx proxies `/api` to
the backend. For a separately hosted API, use a browser-reachable URL such as
`https://api.example.edu/api`, never `http://backend:8000`. Rebuild frontend
after changing it.

## Existing host PostgreSQL database (`external`)

This is the default and is the appropriate mode for the existing MAWOS
database and data. It does not start, mount, initialize, or modify a Docker
PostgreSQL service. Set these values in the ignored `.env`:

```env
MAWOS_DATABASE_MODE=external
# Native host commands, including Alembic:
MAWOS_DATABASE_URL=postgresql+psycopg://mawos_app:YOUR_PASSWORD@127.0.0.1:5432/mawos
# Docker backend container only:
MAWOS_DOCKER_DATABASE_URL=postgresql+psycopg://mawos_app:YOUR_PASSWORD@host.docker.internal:5432/mawos
MAWOS_SEED_DEMO_DATA=false
```

Native commands must retain `127.0.0.1`; Docker containers must use the
separate `host.docker.internal` URL. Compose maps that hostname to Docker's
host gateway on Linux and passes only `MAWOS_DOCKER_DATABASE_URL` to the
backend as its internal `MAWOS_DATABASE_URL`. It stops with a clear config
error if the Docker URL is absent. Ensure the host PostgreSQL listener,
firewall, and PostgreSQL `pg_hba.conf` allow the Docker bridge network; never
substitute the Docker service name.

```bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f backend frontend
```

No migration is run by these commands. The backend startup diagnostic reports
the safe target fields—host, configured database, `current_database()`, and
the Alembic revision—but never a username, password, or URL.

Run native Alembic with the loopback URL from the same `.env`:

```bash
set -a; source .env; set +a
.venv/bin/alembic current
```

## Fresh isolated Docker PostgreSQL (`docker`)

Use this only when an explicitly separate database is intended. Set
`MAWOS_DATABASE_MODE=docker` and `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` in `.env`, retain the documented
`MAWOS_DOCKER_DATABASE_URL` entry, then include the dedicated override. The
override replaces the backend's actual database URL with Docker's private
`postgres` service URL; the gateway URL remains required by the shared base
Compose file so the same `.env` can switch modes explicitly:

```bash
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml build
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml up -d
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml ps
```

Service URLs: frontend `http://localhost:3000`; backend health
`http://localhost:8000/`; API documentation `http://localhost:8000/docs`.
PostgreSQL is intentionally private to the Compose network.

The Docker database uses a persistent named volume, starts empty, and is not a
copy of the existing host database. Backend waits for PostgreSQL health, then
correctly refuses to start until the intended schema/data have been explicitly
restored or initialized. It never creates, resets, seeds, migrates, or removes
the volume automatically. Do not use this mode as a shortcut to the host data.

## Backup, restore, and migrations

Back up the host database before any write operation, using a safe destination
outside the repository. Example (replace the host and database deliberately):

```bash
pg_dump -h 127.0.0.1 -U mawos_app -d mawos --format=custom --file /safe/backups/mawos.backup
```

To restore a reviewed backup into the separate Docker database, first start it
with the Docker-mode commands above, then explicitly run:

```bash
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" < /safe/backups/mawos.backup
```

That restore command changes the target database; confirm it is the isolated
Docker target and that it is empty or otherwise prepared for the backup before
running it. It must never be pointed at the real host database.

After a backup, target verification, and change approval, migrations are an
explicit operator action only:

```bash
docker compose exec backend alembic upgrade head
```

For an existing MAWOS database whose documented baseline is already present,
use the reviewed project procedure (which may require `alembic stamp head`)
rather than blindly upgrading. Never run migration commands automatically or
against production without a backup and change approval.

## Ollama (optional)

Ollama is disabled by default and MAWOS retains its deterministic safe fallback
when unavailable. Enable it and pull the model explicitly:

```bash
docker compose --profile ollama up -d
docker exec -it mawos-ollama-1 ollama pull qwen2.5:3b
```

Use `docker compose ps` to confirm the actual container name if it differs.
No model is downloaded by normal startup.

## Development and lifecycle

Production images are immutable. For hot reload, retain the existing workflow:
`python run.py` and, in `frontend`, `npm run dev`. Vite retains its `/api`
proxy to `http://127.0.0.1:8000`.

```bash
docker compose down                 # stops containers, retains database data
docker compose logs --tail=200 backend
```

If backend health fails, inspect `docker compose logs backend`; usually the
database mode/URL is wrong, or the Docker volume is uninitialized. The startup
diagnostic identifies the safe target metadata needed to correct this. If the UI cannot
call the API, ensure `VITE_API_BASE_URL` is browser-reachable, then run
`docker compose build frontend && docker compose up -d frontend`.

For production, use managed or backup-tested PostgreSQL, a strong unique JWT
secret, TLS at a reverse proxy, pinned images, and external secret management.
Do not publish PostgreSQL or put secrets in images, Compose files, source
control, or build arguments.
