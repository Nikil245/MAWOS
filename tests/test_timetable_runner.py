import pytest
from backend.app.timetable import runner
from test_timetable_solver import toy


def test_real_worker_process_returns_a_valid_result():
    result, ms = runner.run_solver(toy(), max_steps=100000)
    assert result.status == 'COMPLETE' and ms > 0


def test_hard_timeout_terminates_worker_and_releases_capacity():
    with pytest.raises(runner.SolverTimeout):
        runner.run_solver(toy(), timeout=0)
    result, _ = runner.run_solver(toy())
    assert result.status == 'COMPLETE'


def test_concurrency_capacity_is_bounded_and_rejected_without_queuing():
    assert runner._capacity.acquire(False)
    assert runner._capacity.acquire(False)
    try:
        with pytest.raises(runner.SolverBusy): runner.run_solver(toy())
    finally:
        runner._capacity.release()
        runner._capacity.release()
