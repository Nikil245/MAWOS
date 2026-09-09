"""Hard-constraint, reproducibility and adversarial independent-validator tests."""
import datetime as dt
import pytest
from backend.app.timetable.contracts import Entry, Period, Requirement, Room, Section, SolverInput, Teacher
from backend.app.timetable.solver import solve
from backend.app.timetable.validation import preflight, validate
from backend.app.timetable.room_types import room_kind_satisfies
from backend.app.timetable.reads import temporal, TZ


def toy(sections=2):
    return SolverInput(
        periods=tuple(Period(day=d, index=p, start=540+p*60, end=600+p*60, closed=p == 3) for d in range(5) for p in range(6)),
        sections=tuple(Section(id=i, size=30) for i in range(1, sections+1)),
        teachers=(Teacher(id=1, subjects=('MATH',), daily_limit=4, weekly_limit=20), Teacher(id=2, subjects=('LAB',), daily_limit=4, weekly_limit=20)),
        rooms=(Room(id=1, kind='classroom', capacity=40), Room(id=2, kind='lab', capacity=40)),
        requirements=tuple(r for i in range(1, sections+1) for r in (
            Requirement(id=i*10, section_id=i, subject='MATH', faculty_id=1, periods=4, max_per_day=1),
            Requirement(id=i*10+1, section_id=i, subject='LAB', faculty_id=2, periods=4, max_per_day=2, block_length=2, room_type='lab'))))


def aiml_144_period_regression(computer_lab_kind='computer_lab'):
    """Shape of the AIML failure: 8 sections, 40 subjects and 144 periods."""
    subject_groups = [
        ('23AI11', '23AI12', '23AI13', '23AI14', '23AI15'),
        ('23AI11', '23AI12', '23AI13', '23AI14', '23AI15'),
        ('23AI31', '23AI32', '23AI33', '23AI34', '23AI35'),
        ('23AI31', '23AI32', '23AI33', '23AI34', '23AI35'),
        ('23AI51', '23AI52', '23AI53', '23AI54', '23AI55'),
        ('23AI51', '23AI52', '23AI53', '23AI54', '23AI55'),
        ('23AI71', '23AI72', '23AI73', '23AI74', '23AI75'),
        ('23AI71', '23AI72', '23AI73', '23AI74', '23AI75'),
    ]
    faculty_groups = [
        (2, 3, 4, 5, 6), (7, 8, 9, 10, 11),
        (12, 13, 14, 15, 1), (2, 3, 4, 5, 6),
        (7, 8, 9, 10, 11), (12, 13, 14, 15, 1),
        (2, 3, 4, 5, 6), (7, 8, 9, 10, 11),
    ]
    qualifications = {faculty_id: set() for faculty_id in range(1, 16)}
    requirements = []
    requirement_id = 1
    for section_id, (subjects, faculty_ids) in enumerate(zip(subject_groups, faculty_groups), 1):
        for subject, faculty_id, periods in zip(subjects, faculty_ids, (4, 4, 4, 3, 3)):
            qualifications[faculty_id].add(subject)
            requirements.append(Requirement(
                id=requirement_id, section_id=section_id, subject=subject,
                faculty_id=faculty_id, periods=periods, max_per_day=2,
                room_type='classroom'))
            requirement_id += 1
    return SolverInput(
        periods=tuple(Period(day=day, index=index, start=540+index*60, end=600+index*60)
                      for day in range(6) for index in range(6 if day < 5 else 3)),
        sections=tuple(Section(id=section_id, size=60 if section_id == 5 else 30)
                       for section_id in range(1, 9)),
        teachers=tuple(Teacher(id=faculty_id, subjects=tuple(sorted(subjects)),
                               daily_limit=5, weekly_limit=24)
                       for faculty_id, subjects in qualifications.items()),
        rooms=(Room(id=1, kind=computer_lab_kind, capacity=60),
               *(Room(id=room_id, kind='classroom', capacity=60) for room_id in range(2, 6))),
        requirements=tuple(requirements),
    )


def codes(issues):
    return {i.code for i in issues}


@pytest.mark.parametrize('seed', [0, 1, 7, 33, 100])
def test_different_seeds_are_complete_and_valid(seed):
    data = toy()
    result = solve(data, seed=seed)
    assert result.status == 'COMPLETE'
    assert len(result.entries) == 16
    assert not validate(data, result.entries)
    assert result.steps <= 500000
    assert not result.unplaced


def test_same_input_seed_and_metrics_are_identical_and_input_is_immutable():
    data = toy()
    before = data.model_dump_json()
    assert solve(data, seed=19) == solve(data, seed=19)
    assert data.model_dump_json() == before


@pytest.mark.parametrize('seed', [7, 42, 123, 2026])
def test_aiml_144_period_regression_is_complete_for_every_reported_seed(seed):
    data = aiml_144_period_regression()
    result = solve(data, seed=seed)
    assert sum(r.periods for r in data.requirements) == 144
    assert {r.subject for r in data.requirements if r.id in (37, 38, 39, 40)} == {
        '23AI72', '23AI73', '23AI74', '23AI75'}
    assert result.status == 'COMPLETE'
    assert len(result.entries) == 144
    assert not result.unplaced
    assert not validate(data, result.entries)


def test_computer_lab_room_compatibility_is_one_way_and_capacity_is_diagnostic():
    assert room_kind_satisfies('classroom', 'computer_lab')
    assert not room_kind_satisfies('computer_lab', 'classroom')
    data = aiml_144_period_regression(computer_lab_kind='special_lab')
    issues = preflight(data)
    capacity = [issue for issue in issues if issue.code == 'room_capacity']
    assert len(capacity) == 1
    assert 'need 144 room-periods' in capacity[0].message
    assert 'at most 132' in capacity[0].message
    result = solve(data)
    assert result.status == 'FAILED'
    assert result.steps == 0


@pytest.mark.parametrize('field,code', [('teachers', 'missing_teachers'), ('rooms', 'missing_rooms'), ('periods', 'missing_periods'), ('requirements', 'missing_requirements'), ('sections', 'missing_sections')])
def test_missing_inputs_have_actionable_preflight_errors(field, code):
    result = solve(toy().model_copy(update={field: ()}))
    assert result.status == 'FAILED' and result.steps == 0
    assert code in codes(result.violations)
    assert all(i.message for i in result.violations)


@pytest.mark.parametrize('kind', ['qualification', 'room_type', 'capacity', 'faculty_load', 'availability', 'timings', 'lab_block', 'section_load'])
def test_impossible_inputs_fail_preflight(kind):
    data = toy(1)
    if kind == 'qualification':
        data = data.model_copy(update={'teachers': tuple(t.model_copy(update={'subjects': ()}) for t in data.teachers)})
    elif kind == 'room_type':
        data = data.model_copy(update={'rooms': (data.rooms[0],)})
    elif kind == 'capacity':
        data = data.model_copy(update={'rooms': tuple(r.model_copy(update={'capacity': 1}) for r in data.rooms)})
    elif kind == 'faculty_load':
        data = data.model_copy(update={'teachers': tuple(t.model_copy(update={'weekly_limit': 1}) for t in data.teachers)})
    elif kind == 'availability':
        data = data.model_copy(update={'teachers': tuple(t.model_copy(update={'unavailable': tuple((p.day, p.index) for p in data.periods)}) for t in data.teachers)})
    elif kind == 'timings':
        data = data.model_copy(update={'periods': (data.periods[0].model_copy(update={'end': 900}), *data.periods[1:])})
    elif kind == 'lab_block':
        data = data.model_copy(update={'requirements': (data.requirements[0], data.requirements[1].model_copy(update={'periods': 3}))})
    else:
        data = data.model_copy(update={'requirements': tuple(r.model_copy(update={'periods': 80}) for r in data.requirements)})
    issues = preflight(data)
    assert issues
    assert not solve(data).entries


def test_total_budget_is_not_reset_across_restarts_repair_or_polish():
    result = solve(toy(2), max_steps=150, restarts=5, optimize_passes=500)
    assert result.steps <= 150
    assert result.status == 'PARTIAL'
    assert result.unplaced
    assert sum(u.missing_periods for u in result.unplaced) + len(result.entries) == 16
    assert all(u.reason and u.subject and u.section_id for u in result.unplaced)
    assert not validate(toy(2), result.entries, complete=False)


def test_locks_survive_regeneration_including_whole_lab_blocks():
    data = toy()
    original = solve(data)
    block = tuple(e.model_copy(update={'locked': True}) for e in original.entries if (e.requirement_id, e.occurrence) in {(11, 0), (20, 0)})
    fixed = data.model_copy(update={'locked': block})
    regenerated = solve(fixed, seed=88)
    assert regenerated.status == 'COMPLETE'
    assert set(block) <= set(regenerated.entries)
    assert not validate(fixed, regenerated.entries)


def test_invalid_or_partial_locks_are_rejected():
    data = toy()
    lab = next(e for e in solve(data).entries if e.subject == 'LAB')
    assert 'lab_block' in codes(preflight(data.model_copy(update={'locked': (lab,)})))
    wrong = lab.model_copy(update={'room_id': 999})
    assert 'reference' in codes(preflight(data.model_copy(update={'locked': (wrong,)})))


def test_lab_blocks_do_not_cross_time_gaps_breaks_or_days():
    data = toy()
    result = solve(data)
    for rid in (11, 21):
        for occurrence in (0, 1):
            block = [e for e in result.entries if (e.requirement_id, e.occurrence) == (rid, occurrence)]
            assert len({(e.day, e.room_id) for e in block}) == 1
            assert block[1].period_index == block[0].period_index+1
            assert all(e.period_index != 3 for e in block)
    gap = data.model_copy(update={'periods': tuple(p.model_copy(update={'end': p.end-10}) for p in data.periods)})
    assert 'availability' in codes(preflight(gap))


def test_room_and_faculty_unavailability_are_respected():
    data = toy()
    unavailable = ((0, 0), (0, 1), (1, 0), (1, 1))
    data = data.model_copy(update={'rooms': tuple(r.model_copy(update={'unavailable': unavailable}) for r in data.rooms),
                                   'teachers': tuple(t.model_copy(update={'unavailable': ((2, 0),)}) for t in data.teachers)})
    result = solve(data)
    assert result.status == 'COMPLETE'
    assert all((e.day, e.period_index) not in unavailable + ((2, 0),) for e in result.entries)


@pytest.mark.parametrize('change,code', [({'faculty_id': 2}, 'assignment'), ({'subject': 'LAB'}, 'qualification'),
    ({'room_id': 2}, 'room_type'), ({'period_index': 3}, 'closed_period'), ({'period_index': 99}, 'closed_period'),
    ({'section_id': 999}, 'reference'), ({'occurrence': 999}, 'lab_block')])
def test_independent_validator_rejects_forged_entries(change, code):
    data = toy(1)
    entries = list(solve(data).entries)
    entries[0] = entries[0].model_copy(update=change)
    assert code in codes(validate(data, tuple(entries)))


@pytest.mark.parametrize('code', ['section_collision', 'faculty_collision', 'room_collision'])
def test_validator_detects_each_collision(code):
    data = toy()
    entries = solve(data).entries
    assert code in codes(validate(data, entries+(entries[0],)))


@pytest.mark.parametrize('constraint', ['capacity', 'daily_load', 'weekly_load', 'subject_daily', 'faculty_unavailable', 'room_unavailable', 'locked_entry', 'weekly_periods'])
def test_validator_detects_all_other_constraints(constraint):
    data = toy(1)
    entries = solve(data).entries
    e = entries[0]
    if constraint == 'capacity':
        data = data.model_copy(update={'rooms': tuple(r.model_copy(update={'capacity': 1}) for r in data.rooms)})
    elif constraint in ('daily_load', 'weekly_load'):
        key = 'daily_limit' if constraint == 'daily_load' else 'weekly_limit'
        data = data.model_copy(update={'teachers': tuple(t.model_copy(update={key: 1}) for t in data.teachers)})
    elif constraint == 'subject_daily':
        data = data.model_copy(update={'requirements': tuple(r.model_copy(update={'max_per_day': 1}) for r in data.requirements)})
    elif constraint == 'faculty_unavailable':
        data = data.model_copy(update={'teachers': tuple(t.model_copy(update={'unavailable': ((e.day, e.period_index),)}) for t in data.teachers)})
    elif constraint == 'room_unavailable':
        data = data.model_copy(update={'rooms': tuple(r.model_copy(update={'unavailable': ((e.day, e.period_index),)}) for r in data.rooms)})
    elif constraint == 'locked_entry':
        data = data.model_copy(update={'locked': (e,)})
        entries = entries[1:]
    else:
        entries = entries[1:]
    assert constraint in codes(validate(data, entries))


def temporal_entry(day=0, start=540, end=600):
    return {'day': day, 'start_minute': start, 'end_minute': end, 'subject_code': 'MATH'}


@pytest.mark.parametrize('minute,current,next_exists', [(539, False, True), (540, True, True), (599, True, True), (600, False, True)])
def test_current_class_half_open_period_boundaries(minute, current, next_exists):
    now = dt.datetime(2026, 9, 7, minute//60, minute%60, tzinfo=TZ)
    result = temporal([temporal_entry()], now, ends_on=dt.date(2026, 9, 21))
    assert bool(result['current']) == current
    assert bool(result['next']) == next_exists


def test_next_class_continues_to_next_working_day_and_skips_holidays():
    now = dt.datetime(2026, 9, 11, 18, tzinfo=TZ)  # Friday
    result = temporal([temporal_entry()], now, holidays=[dt.date(2026, 9, 14)], ends_on=dt.date(2026, 9, 30))
    assert result['next']['date'] == '2026-09-21'
    assert result['next']['starts_at'].endswith('+05:30')


def test_utc_now_and_term_end_empty_state():
    result = temporal([temporal_entry()], dt.datetime(2026, 9, 7, 3, 30, tzinfo=dt.timezone.utc), ends_on=dt.date(2026, 9, 7))
    assert result['current'] and result['next'] is None
    with pytest.raises(ValueError):
        temporal([], dt.datetime(2026, 9, 7))
