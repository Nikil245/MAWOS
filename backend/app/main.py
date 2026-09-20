"""MAWOS API — event-driven institutional workflow orchestration."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config, llm
from . import router as hybrid_router
from .agents import get_agents
from .api.routes import router
from .database import Base, engine, startup_diagnostic, verify_existing_schema
from .seed import bootstrap_evaluations, seed_all

PROACTIVE_INTERVAL_S = 300  # agents run their own scans every 5 minutes


async def _proactive_loop(agents):
    while True:
        await asyncio.sleep(PROACTIVE_INTERVAL_S)
        try:
            await agents["attendance_agent"].proactive_scan()
            await agents["finance_agent"].proactive_scan()
        except Exception as exc:  # keep the loop alive
            print(f"[MAWOS] proactive scan error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate before touching the database or creating background work.
    config.validate_security_configuration()
    config.validate_database_configuration()
    database_backend = config.database_backend()
    database_mode = config.database_mode()
    diagnostic = startup_diagnostic()
    print("[MAWOS] database target: "
          f"mode={database_mode}, host={diagnostic['host']}, "
          f"database={diagnostic['database']}, "
          f"current_database={diagnostic['current_database']}, "
          f"migration_revision={diagnostic['migration_revision']}")
    if database_backend == "sqlite":
        # SQLite remains supported for local development and isolated tests.
        Base.metadata.create_all(bind=engine)
    else:
        # PostgreSQL schema is managed by reviewed Alembic migrations, never startup.
        verify_existing_schema()
    print(f"[MAWOS] database backend: {database_backend}")
    agents = get_agents()
    if database_backend == "sqlite" and config.seed_demo_data_enabled():
        freshly_seeded = seed_all()
        if freshly_seeded:
            print("[MAWOS] fresh demo data seeded — bootstrapping evaluations…")
            bootstrap_evaluations(agents)
    # Providers are optional and checked only for eligible generative turns;
    # application startup must never block on or depend on either one.
    mode = (f"hybrid router, tau {hybrid_router.TAU:.2f}, provider policy "
            f"{config.AI_PROVIDER}, providers checked on demand")
    print(f"[MAWOS] {len(agents)} agents online · AI mode: {mode}")
    task = asyncio.create_task(_proactive_loop(agents))
    yield
    task.cancel()


app = FastAPI(title="MAWOS", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.cors_origins()),
    allow_credentials=False,  # MAWOS uses Authorization: Bearer, never cookies.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)
from .timetable.api import router as timetable_router
from .timetable import reads  # Register published-view routes.
app.include_router(timetable_router)
app.include_router(router)
from .placement.api import router as placement_router
app.include_router(placement_router)
from .campus_events import router as campus_events_router
app.include_router(campus_events_router)
from .parent_portal import router as parent_portal_router
app.include_router(parent_portal_router)


@app.get("/", tags=["service"])
def service_status():
    """Backend-only health/status endpoint; the React app runs via Vite."""
    return {"service": "MAWOS API", "status": "running", "docs": "/docs"}

from .library.api import router as library_router
app.include_router(library_router)
from .coverage.api import router as coverage_router
app.include_router(coverage_router)
