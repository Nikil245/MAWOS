"""Bounded CPU execution off FastAPI's event loop, with a hard process deadline."""
import multiprocessing
import threading
import time
from .solver import solve

_capacity = threading.BoundedSemaphore(2)


class SolverBusy(RuntimeError):
    pass


class SolverTimeout(RuntimeError):
    pass


def _worker(connection, data, options):
    try:
        connection.send(('ok', solve(data, **options)))
    except Exception:
        connection.send(('error', None))
    finally:
        connection.close()


def run_solver(data, *, timeout=20, **options):
    if not _capacity.acquire(blocking=False):
        raise SolverBusy('Two timetable jobs are already running. Retry shortly.')
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(writer, data, options), daemon=True)
    started = time.monotonic()
    try:
        process.start()
        writer.close()
        if not reader.poll(timeout):
            raise SolverTimeout('Search exceeded its time limit. Reduce scope or search budget and retry.')
        state, result = reader.recv()
        if state != 'ok':
            raise RuntimeError('Timetable computation failed.')
        return result, round((time.monotonic()-started)*1000, 2)
    finally:
        if process.pid:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        reader.close()
        writer.close()
        _capacity.release()
