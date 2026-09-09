"""Independent exhaustive validation. Never calls solver feasibility code."""
from collections import Counter, defaultdict
from .contracts import Entry, Issue, SolverInput
from .room_types import room_kind_satisfies


def validate(data: SolverInput, entries: tuple[Entry, ...], *, complete=True) -> list[Issue]:
    issues = []
    def bad(code, message, rid=None):
        issues.append(Issue(code=code, message=message, requirement_id=rid))
    requirements = {r.id: r for r in data.requirements}
    sections = {s.id: s for s in data.sections}
    teachers = {t.id: t for t in data.teachers}
    rooms = {r.id: r for r in data.rooms}
    periods = {(p.day, p.index): p for p in data.periods}
    occupied = set()
    daily, weekly, subject_day, counts = Counter(), Counter(), Counter(), Counter()
    blocks = defaultdict(list)
    for e in entries:
        r = requirements.get(e.requirement_id)
        t, room, section = teachers.get(e.faculty_id), rooms.get(e.room_id), sections.get(e.section_id)
        p = periods.get((e.day, e.period_index))
        if not r or not t or not room or not section:
            bad('reference', 'Entry references an unknown requirement, section, teacher or room.', e.requirement_id)
            continue
        if (e.section_id, e.subject, e.faculty_id) != (r.section_id, r.subject, r.faculty_id):
            bad('assignment', 'Entry must match the section-subject teaching assignment.', r.id)
        if e.subject not in t.subjects:
            bad('qualification', 'Assign a faculty member qualified for this subject.', r.id)
        if not p or p.closed:
            bad('closed_period', 'Choose an open teaching period, outside breaks.', r.id)
        if (e.day, e.period_index) in t.unavailable:
            bad('faculty_unavailable', 'Faculty is unavailable in this period.', r.id)
        if (e.day, e.period_index) in room.unavailable:
            bad('room_unavailable', 'Room is unavailable in this period.', r.id)
        if not room_kind_satisfies(r.room_type, room.kind):
            bad('room_type', f'Requires a {r.room_type} room.', r.id)
        if room.capacity < section.size:
            bad('capacity', f'Provide a room with capacity at least {section.size}.', r.id)
        for kind, identity in [('section', e.section_id), ('faculty', e.faculty_id), ('room', e.room_id)]:
            key = (kind, identity, e.day, e.period_index)
            if key in occupied:
                bad(f'{kind}_collision', f'{kind.title()} has overlapping classes.', r.id)
            occupied.add(key)
        daily[e.faculty_id, e.day] += 1
        weekly[e.faculty_id] += 1
        subject_day[r.id, e.day] += 1
        counts[r.id] += 1
        blocks[r.id, e.occurrence].append(e)
    for (fid, day), count in daily.items():
        if count > teachers[fid].daily_limit:
            bad('daily_load', f'Faculty {fid} exceeds daily load on day {day}.')
    for fid, count in weekly.items():
        if count > teachers[fid].weekly_limit:
            bad('weekly_load', f'Faculty {fid} exceeds weekly load.')
    for (rid, day), count in subject_day.items():
        if count > requirements[rid].max_per_day:
            bad('subject_daily', f'Subject exceeds its daily limit on day {day}.', rid)
    for r in data.requirements:
        if counts[r.id] > r.periods or (complete and counts[r.id] != r.periods):
            bad('weekly_periods', f'Requires {r.periods} periods; found {counts[r.id]}.', r.id)
    for (rid, occurrence), block in blocks.items():
        r = requirements[rid]
        ordered = sorted(block, key=lambda e: e.period_index)
        valid = (len(block) == r.block_length and occurrence < r.periods // r.block_length
                 and len({(e.day, e.room_id, e.faculty_id, e.locked) for e in block}) == 1)
        for a, b in zip(ordered, ordered[1:]):
            pa, pb = periods.get((a.day, a.period_index)), periods.get((b.day, b.period_index))
            valid = valid and b.period_index == a.period_index + 1 and pa is not None and pb is not None and pa.end == pb.start
        if not valid:
            bad('lab_block', 'Every occurrence must form a complete consecutive block in one room; lock the entire block.', rid)
    # Compare all identity/position fields, not merely the number of locks.
    actual = {e.model_copy(update={'locked': True}) for e in entries}
    for e in data.locked:
        if e.model_copy(update={'locked': True}) not in actual:
            bad('locked_entry', 'A fixed entry was moved or removed.', e.requirement_id)
    return issues


def preflight(data: SolverInput) -> list[Issue]:
    errors = []
    def bad(code, message, rid=None):
        errors.append(Issue(code=code, message=message, requirement_id=rid))
    for name in ('periods', 'sections', 'teachers', 'rooms', 'requirements'):
        if not getattr(data, name):
            bad(f'missing_{name}', f'Configure {name} before generation.')
    for name in ('sections', 'teachers', 'rooms', 'requirements'):
        values = getattr(data, name)
        if len({v.id for v in values}) != len(values):
            bad('duplicate_id', f'Duplicate identifiers in {name}.')
    periods = {(p.day, p.index): p for p in data.periods}
    if len(periods) != len(data.periods):
        bad('timings', 'Period day/index pairs must be unique.')
    for day in range(7):
        day_periods = sorted((p for p in data.periods if p.day == day), key=lambda p: p.index)
        for i, p in enumerate(day_periods):
            if p.start >= p.end or (i and day_periods[i-1].end > p.start):
                bad('timings', 'Periods must have ordered, non-overlapping start/end times.')
    teachers, sections = {t.id: t for t in data.teachers}, {s.id: s for s in data.sections}
    teacher_load, section_load = Counter(), Counter()
    seen = set()
    for r in data.requirements:
        t, s = teachers.get(r.faculty_id), sections.get(r.section_id)
        if (r.section_id, r.subject) in seen:
            bad('duplicate_requirement', 'Keep one requirement per section and subject.', r.id)
        seen.add((r.section_id, r.subject))
        if not s or not t:
            bad('assignment', 'Configure a valid section and assigned teacher.', r.id)
            continue
        if r.subject not in t.subjects:
            bad('qualification', 'Record qualification for the assigned teacher and subject.', r.id)
        if r.periods % r.block_length or r.block_length > r.max_per_day:
            bad('lab_block', 'Weekly periods must divide into whole blocks within the daily limit.', r.id)
        compatible = [room for room in data.rooms if room.capacity >= s.size and room_kind_satisfies(r.room_type, room.kind)]
        if not compatible:
            bad('rooms', f'Add a {r.room_type} room with capacity >= {s.size}.', r.id)
        possible = []
        for p in data.periods:
            block = [periods.get((p.day, p.index + j)) for j in range(r.block_length)]
            if any(b is None or b.closed or (b.day, b.index) in t.unavailable for b in block):
                continue
            if any(a.end != b.start for a, b in zip(block, block[1:])):
                continue
            if any(all((b.day, b.index) not in room.unavailable for b in block) for room in compatible):
                possible.append(p.day)
        if not possible:
            bad('availability', 'No consecutive open periods with an available teacher and compatible room.', r.id)
        if len(set(possible)) * r.max_per_day < r.periods:
            bad('subject_capacity', 'Weekly demand exceeds available days times the subject daily limit.', r.id)
        teacher_load[t.id] += r.periods
        section_load[s.id] += r.periods
    for tid, count in teacher_load.items():
        t = teachers[tid]
        available = Counter(p.day for p in data.periods if not p.closed and (p.day, p.index) not in t.unavailable)
        if count > min(t.weekly_limit, sum(min(n, t.daily_limit) for n in available.values())):
            bad('faculty_load', f'Reduce assigned demand or increase allowed availability/load for faculty {tid}.')
    for sid, count in section_load.items():
        if count > sum(not p.closed for p in data.periods):
            bad('section_load', f'Section {sid} requires more periods than the weekly template provides.')
    # Detect an elementary shared-room bottleneck before search. This is a
    # necessary (not speculative) capacity bound for each required room kind.
    for required_kind in sorted({r.room_type for r in data.requirements}):
        relevant = [r for r in data.requirements if r.room_type == required_kind]
        compatible_room_ids = {
            room.id for room in data.rooms
            if room_kind_satisfies(required_kind, room.kind)
            and any(room.capacity >= sections[r.section_id].size for r in relevant if r.section_id in sections)
        }
        available_cells = sum(
            1 for room in data.rooms if room.id in compatible_room_ids
            for p in data.periods
            if not p.closed and (p.day, p.index) not in room.unavailable
        )
        demand = sum(r.periods for r in relevant)
        if demand > available_cells:
            bad('room_capacity',
                f'{required_kind} requirements need {demand} room-periods but compatible rooms provide at most {available_cells}.')
    # Invalid references/timings must be fixed before evaluating locks.
    if not errors:
        errors.extend(validate(data, data.locked, complete=False))
    return errors
