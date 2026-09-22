"""Narrow, provider-neutral input boundary for timetable generation.

Only the native source is enabled. A future licensed integration must map a
documented vendor export/API into the same validated DomainInput contract; it
must not receive a SQLAlchemy session or become a publication path.
"""
from typing import Protocol

from . import service


class TimetableGenerationSource(Protocol):
    name: str

    def snapshot(self, db, term_id: int, dept_code: str): ...


class NativeGenerationSource:
    name = 'mawos-native'

    def snapshot(self, db, term_id: int, dept_code: str):
        return service.snapshot(db, term_id, dept_code)


native_source: TimetableGenerationSource = NativeGenerationSource()
