#!/usr/bin/env python3
"""Preview/apply timetable configuration derived from existing MAWOS rows.

The default is dry-run.  Weekly demand is intentionally left unresolved unless
--weekly-periods or --use-subject-credits is supplied. Existing requirements win.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="preview only (default)")
    mode.add_argument("--apply", action="store_true", help="insert missing timetable configuration")
    p.add_argument("--term-id", type=int, required=True)
    p.add_argument("--department", action="append", default=[], metavar="DEPT_CODE")
    weekly = p.add_mutually_exclusive_group()
    weekly.add_argument("--weekly-periods", type=int, help="explicit periods/week for new requirements")
    weekly.add_argument("--use-subject-credits", action="store_true",
                        help="explicitly use existing subject credits as periods/week")
    p.add_argument("--max-per-day", type=int, default=2,
                   help="new-requirement scheduling limit (default: 2; always reported)")
    p.add_argument("--block-length", type=int, default=1,
                   help="new-requirement block length (default: 1; always reported)")
    p.add_argument("--room-type", default="classroom",
                   help="required room kind for new requirements (default: classroom; always reported)")
    p.add_argument("--faculty-daily-limit", type=int)
    p.add_argument("--faculty-weekly-limit", type=int)
    p.add_argument("--create-placeholder-rooms", action="store_true")
    p.add_argument("--confirm-placeholder-rooms", action="store_true",
                   help="confirm placeholder labels are not real physical-room records")
    return p


def main() -> int:
    args = parser().parse_args()
    # Import only after the caller has loaded the intended environment.
    from backend.app import config
    from backend.app.timetable.bootstrap import BootstrapOptions, bootstrap

    url = make_url(config.DATABASE_URL)
    if url.get_backend_name() != "postgresql":
        raise SystemExit("Timetable bootstrap requires an explicit PostgreSQL MAWOS_DATABASE_URL")
    engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    try:
        with Session() as db:
            database = db.execute(text("SELECT current_database()" )).scalar_one()
            if database not in {"mawos", "mawos_test"}:
                raise SystemExit("Refusing unexpected database; expected mawos or mawos_test")
            if not args.apply:
                db.execute(text("SET TRANSACTION READ ONLY"))
            configured_weekly = args.weekly_periods
            weekly_source = "explicit --weekly-periods"
            if configured_weekly is None and not args.use_subject_credits:
                raw_default = os.getenv("MAWOS_TIMETABLE_DEFAULT_WEEKLY_PERIODS", "").strip()
                if raw_default:
                    try:
                        configured_weekly = int(raw_default)
                    except ValueError:
                        raise SystemExit("MAWOS_TIMETABLE_DEFAULT_WEEKLY_PERIODS must be an integer") from None
                    weekly_source = "configured MAWOS_TIMETABLE_DEFAULT_WEEKLY_PERIODS"
            options = BootstrapOptions(weekly_periods=configured_weekly,
                weekly_period_source=weekly_source,
                use_subject_credits=args.use_subject_credits, max_per_day=args.max_per_day,
                block_length=args.block_length, room_type=args.room_type,
                faculty_daily_limit=args.faculty_daily_limit,
                faculty_weekly_limit=args.faculty_weekly_limit,
                create_placeholder_rooms=args.create_placeholder_rooms,
                confirm_placeholder_rooms=args.confirm_placeholder_rooms)
            try:
                report = bootstrap(db, args.term_id, departments=args.department,
                                   apply=args.apply, options=options)
                if args.apply:
                    db.commit()
                else:
                    db.rollback()
            except Exception:
                db.rollback()
                raise
            print(json.dumps({"database": database, **report}, indent=2, default=str))
            return 0 if not report["conflicts"] else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
