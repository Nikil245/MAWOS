"""Clean Python adaptation: MRV search, bounded repair and hill climbing.

Pure and deterministic: no database, clock, environment or mutable global state.
A step is one candidate feasibility evaluation (including MRV and repair).
All phases and restarts consume the same budget. The runner enforces wall time.
"""
from collections import Counter, defaultdict
import random
from .contracts import Entry, Result, SolverInput, Unplaced
from .room_types import room_kind_satisfies
from .validation import preflight, validate


def solve(data: SolverInput, seed: int = 7, max_steps: int = 500_000,
          restarts: int = 2, optimize_passes: int = 100) -> Result:
    if max_steps < 1 or restarts < 0 or optimize_passes < 0:
        raise ValueError('Solver budgets must be positive/nonnegative.')
    errors = preflight(data)
    requirements = {r.id: r for r in data.requirements}
    def missing(entries, reason):
        counts = Counter(e.requirement_id for e in entries)
        return tuple(Unplaced(requirement_id=r.id, section_id=r.section_id, subject=r.subject,
                              missing_periods=r.periods-counts[r.id], reason=reason)
                     for r in data.requirements if counts[r.id] < r.periods)
    if errors:
        return Result(status='FAILED', entries=(), violations=tuple(errors), unplaced=missing((), 'Fix preflight errors.'),
                      steps=0, search_conflicts=0, backtracks=0, restarts=0, score=0, termination='preflight')
    rng = random.Random(seed)
    periods = {(p.day, p.index): p for p in data.periods}
    teachers = {t.id: t for t in data.teachers}
    sections = {s.id: s for s in data.sections}
    rooms = {r.id: r for r in data.rooms}
    domains = {}
    for r in data.requirements:
        candidates = []
        t = teachers[r.faculty_id]
        for p in sorted(data.periods, key=lambda p: (p.day, p.index)):
            block = [periods.get((p.day, p.index+j)) for j in range(r.block_length)]
            if any(b is None or b.closed or (b.day, b.index) in t.unavailable for b in block):
                continue
            if any(a.end != b.start for a, b in zip(block, block[1:])):
                continue
            for room in sorted(data.rooms, key=lambda room: (room.capacity, room.id)):
                if room.capacity < sections[r.section_id].size or not room_kind_satisfies(r.room_type, room.kind):
                    continue
                if any((b.day, b.index) in room.unavailable for b in block):
                    continue
                candidates.append((p.day, p.index, room.id))
        domains[r.id] = candidates
    keys = [(r.id, n) for r in data.requirements for n in range(r.periods // r.block_length)]
    fixed = {(e.requirement_id, e.occurrence): (e.day, e.period_index, e.room_id)
             for e in sorted(data.locked, key=lambda e: -e.period_index)}
    placement = {}
    occupied = set()
    day_load, week_load, subject_load = Counter(), Counter(), Counter()
    steps = conflicts = backtracks = restart_count = 0

    def cells(key, candidate):
        r = requirements[key[0]]
        d, p, room = candidate
        return [(kind, identity, d, p+j) for j in range(r.block_length)
                for kind, identity in [('s', r.section_id), ('f', r.faculty_id), ('r', room)]]

    def feasible(key, candidate, limit):
        nonlocal steps, conflicts
        if steps >= limit:
            return False
        steps += 1
        r = requirements[key[0]]
        t = teachers[r.faculty_id]
        day = candidate[0]
        ok = (day_load[t.id, day] + r.block_length <= t.daily_limit
              and week_load[t.id] + r.block_length <= t.weekly_limit
              and subject_load[r.id, day] + r.block_length <= r.max_per_day
              and not any(c in occupied for c in cells(key, candidate)))
        if not ok:
            conflicts += 1
        return ok

    def put(key, candidate):
        placement[key] = candidate
        occupied.update(cells(key, candidate))
        r = requirements[key[0]]
        day_load[r.faculty_id, candidate[0]] += r.block_length
        week_load[r.faculty_id] += r.block_length
        subject_load[r.id, candidate[0]] += r.block_length

    def remove(key):
        c = placement.pop(key)
        occupied.difference_update(cells(key, c))
        r = requirements[key[0]]
        day_load[r.faculty_id, c[0]] -= r.block_length
        week_load[r.faculty_id] -= r.block_length
        subject_load[r.id, c[0]] -= r.block_length
        return c

    def restore(snapshot):
        for k in list(placement):
            remove(k)
        for k, c in snapshot.items():
            put(k, c)

    def entries():
        result = []
        for (rid, n), (day, p, room) in sorted(placement.items()):
            r = requirements[rid]
            result.extend(Entry(requirement_id=rid, occurrence=n, section_id=r.section_id,
                                subject=r.subject, faculty_id=r.faculty_id, room_id=room,
                                day=day, period_index=p+j, locked=(rid, n) in fixed)
                          for j in range(r.block_length))
        return tuple(result)

    def score():
        schedules = defaultdict(list)
        cost = 0
        for (rid, _), (day, p, room) in placement.items():
            r = requirements[rid]
            for j in range(r.block_length):
                schedules['f', r.faculty_id, day].append(p+j)
                schedules['s', r.section_id, day].append(p+j)
                cost += r.priority * (p+j) * .15
            if r.preferred_room_type and rooms[room].kind != r.preferred_room_type:
                cost += 2
        loads = defaultdict(list)
        for (kind, identity, day), ps in schedules.items():
            cost += (max(ps)-min(ps)+1-len(ps)) * 3
            loads[kind, identity].append(len(ps))
            if kind == 'f':
                streak = 0
                for p in range(max(ps)+1):
                    streak = streak+1 if p in ps else 0
                    cost += max(0, streak-3)
        for counts in loads.values():
            cost += sum(c*c for c in counts) * .2
        cost += sum(max(0, n-1) for n in subject_load.values()) * 2
        return round(cost, 4)

    restore(fixed)
    best = dict(placement)
    best_count = sum(requirements[k[0]].block_length for k in best)
    search_budget = max(1, int(max_steps * .8))
    for attempt in range(restarts + 1):
        if len(best) == len(keys) or steps >= search_budget:
            break
        restart_count = attempt
        restore(fixed)
        limit = search_budget * (attempt+1) // (restarts+1)
        stack = []
        while steps < limit:
            remaining = [k for k in keys if k not in placement]
            if not remaining:
                best = dict(placement)
                break
            # Identical occurrences have identical domains: inspect one per requirement.
            representatives = {}
            for k in remaining:
                representatives.setdefault(k[0], k)
            selected, candidates = None, None
            for key in representatives.values():
                choices = []
                feasible_slots = set()
                for c in domains[key[0]]:
                    # Room alternatives at the same time are collapsed to the
                    # smallest available compatible room for MRV. Repair can
                    # still relocate to every room in the full domain.
                    if c[:2] in feasible_slots:
                        continue
                    if feasible(key, c, limit):
                        choices.append(c)
                        feasible_slots.add(c[:2])
                    if steps >= limit or (candidates is not None and len(choices) > len(candidates)):
                        break
                if steps >= limit:
                    break
                if candidates is None or len(choices) < len(candidates):
                    selected, candidates = key, choices
                if not choices:
                    break
            if steps >= limit:
                break
            if candidates:
                r = requirements[selected[0]]
                candidates.sort(key=lambda c: (subject_load[r.id, c[0]]*4 + day_load[r.faculty_id, c[0]]
                                                + c[1]*r.priority*.15 + rng.random()*2))
                put(selected, candidates[0])
                stack.append([selected, candidates, 0])
                count = sum(requirements[k[0]].block_length for k in placement)
                if count > best_count:
                    best, best_count = dict(placement), count
                continue
            # Chronological backtracking; every attempted alternative is budgeted.
            recovered = False
            while stack and steps < limit:
                key, choices, i = stack.pop()
                remove(key)
                backtracks += 1
                for j in range(i+1, len(choices)):
                    if feasible(key, choices[j], limit):
                        put(key, choices[j])
                        stack.append([key, choices, j])
                        recovered = True
                        break
                    if steps >= limit:
                        break
                if recovered:
                    break
            if not recovered:
                break
    restore(best)
    # Bounded repair: eject at most one unlocked block, insert missing work,
    # then reinsert the displaced block. Accept only increases in coverage.
    repair_limit = max(search_budget, int(max_steps*.95))
    for target in keys:
        if target in placement or steps >= repair_limit:
            continue
        snapshot = dict(placement)
        repaired = False
        for c in domains[target[0]]:
            if steps >= repair_limit:
                break
            if feasible(target, c, repair_limit):
                put(target, c)
                repaired = True
                break
            blockers = [k for k, pos in placement.items() if set(cells(k, pos)) & set(cells(target, c))]
            if len(blockers) != 1 or blockers[0] in fixed:
                continue
            displaced = blockers[0]
            old = remove(displaced)
            if feasible(target, c, repair_limit):
                put(target, c)
                for new in domains[displaced[0]]:
                    if feasible(displaced, new, repair_limit):
                        put(displaced, new)
                        repaired = True
                        break
                    if steps >= repair_limit:
                        break
                if repaired:
                    break
                remove(target)
            put(displaced, old)
        if not repaired:
            restore(snapshot)
    # Hill climbing moves entire blocks and never moves locks.
    cost = score()
    movable = [k for k in placement if k not in fixed]
    for _ in range(optimize_passes):
        if not movable or steps >= max_steps:
            break
        key = rng.choice(movable)
        candidate = rng.choice(domains[key[0]])
        old = remove(key)
        if feasible(key, candidate, max_steps):
            put(key, candidate)
            after = score()
            if after < cost:
                cost = after
                continue
            remove(key)
        put(key, old)
    result_entries = entries()
    violations = tuple(validate(data, result_entries))
    unplaced = missing(result_entries, 'No placement within the total search budget; review capacity, availability and locks or try another seed.')
    return Result(status='COMPLETE' if not violations else 'PARTIAL', entries=result_entries,
                  violations=violations, unplaced=unplaced, steps=steps, search_conflicts=conflicts,
                  backtracks=backtracks, restarts=restart_count, score=cost,
                  termination='complete' if not unplaced else 'search_limit')
