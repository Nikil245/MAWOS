"""MAWOS API — event-driven institutional workflow orchestration."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config, llm
from . import router as hybrid_router
from .agents import get_agents
from .api.routes import router
from .database import Base, SessionLocal, engine, verify_existing_schema
from .models import TimetableSlot
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
    if database_backend == "sqlite":
        db = SessionLocal()
        try:
            if db.query(TimetableSlot).count() == 0:
                print("[MAWOS] generating institution timetable…")
                result = agents["timetable_agent"].generate(db)
                print(f"[MAWOS] timetable: {result['slots_placed']} slots, "
                      f"{result['placement_rate']}% placed, "
                      f"{result['solve_ms']} ms")
        finally:
            db.close()
    # Ollama is optional and is checked only for a low-confidence chat turn;
    # application startup must never block on or depend on it.
    mode = (f"hybrid router, tau {hybrid_router.TAU:.2f}, local runtime model "
            f"{config.OLLAMA_MODEL} checked on demand")
    print(f"[MAWOS] {len(agents)} agents online · AI mode: {mode}")
    task = asyncio.create_task(_proactive_loop(agents))
    yield
    task.cancel()


app = FastAPI(title="MAWOS", version="2.0.0", lifespan=lifespan)
app.include_router(router)


@app.get("/", tags=["service"])
def service_status():
    """Backend-only health/status endpoint; the React app runs via Vite."""
    return {"service": "MAWOS API", "status": "running", "docs": "/docs"}
