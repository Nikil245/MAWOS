"""Pure synthetic multi-section benchmark; performs no database access."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.timetable.contracts import Period, Requirement, Room, Section, SolverInput, Teacher
from backend.app.timetable.solver import solve
from backend.app.timetable.validation import validate


def dataset(section_count=12):
    faculty_count = max(10, section_count*2)
    teachers = tuple(Teacher(id=f, subjects=tuple(f'S{j}' for j in range(5)), daily_limit=6, weekly_limit=30) for f in range(faculty_count))
    return SolverInput(
        periods=tuple(Period(day=d, index=p, start=540+p*60, end=600+p*60, closed=p == 3) for d in range(5) for p in range(7)),
        sections=tuple(Section(id=s, size=60) for s in range(section_count)),
        teachers=teachers,
        rooms=tuple(Room(id=r, kind='lab' if r < max(2, section_count//4) else 'classroom', capacity=60) for r in range(section_count+2)),
        requirements=tuple(Requirement(id=s*5+j, section_id=s, subject=f'S{j}', faculty_id=(s*5+j)%faculty_count,
                           periods=2 if j == 4 else 4, max_per_day=2 if j == 4 else 1,
                           block_length=2 if j == 4 else 1, room_type='lab' if j == 4 else 'classroom', priority=5-j)
                           for s in range(section_count) for j in range(5)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sections', type=int, default=12)
    parser.add_argument('--steps', type=int, default=500000)
    parser.add_argument('--seeds', type=int, nargs='+', default=[7, 19, 41])
    args = parser.parse_args()
    data = dataset(args.sections)
    results = []
    for seed in args.seeds:
        start = time.perf_counter()
        result = solve(data, seed=seed, max_steps=args.steps)
        ms = round((time.perf_counter()-start)*1000, 2)
        results.append({'sections': args.sections, 'faculty': len(data.teachers), 'rooms': len(data.rooms),
                        'required': sum(r.periods for r in data.requirements), 'placed': len(result.entries),
                        'seed': seed, 'status': result.status, 'steps': result.steps, 'wall_ms': ms,
                        'hard_violations': len(validate(data, result.entries)), 'score': result.score})
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
