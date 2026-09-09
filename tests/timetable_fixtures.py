"""Explicit test-only college configuration; never imported by runtime code."""
import datetime as dt
import uuid
from backend.app.models import Department, Faculty, Student, Subject, TeachingAssignment, User
from backend.app.timetable import models as m


def college(db):
    dept, other = 'T'+uuid.uuid4().hex[:6].upper(), 'X'+uuid.uuid4().hex[:6].upper()
    db.add_all([Department(code=dept, name='Timetable test', intake=60), Department(code=other, name='Other test', intake=60)])
    db.flush()
    fac = Faculty(name='Qualified Teacher', dept_code=dept)
    outside = Faculty(name='Other Teacher', dept_code=other)
    db.add_all([fac, outside])
    db.flush()
    code = 'SUB'+uuid.uuid4().hex[:8]
    sub = Subject(code=code, name='Test Algorithms', dept_code=dept, semester=5, credits=4)
    db.add(sub)
    db.flush()
    assignment = TeachingAssignment(faculty_id=fac.id, subject_code=code, dept_code=dept, year=3, section='A')
    db.add(assignment)
    db.flush()
    student = Student(usn='TT'+uuid.uuid4().hex[:12], name='Timetable student', dept_code=dept, year=3, semester=5, section='A', cgpa=8)
    db.add(student)
    db.flush()
    users = {}
    for role in ('student', 'faculty', 'hod', 'admin', 'principal'):
        user = User(username=f'{dept}.{role}', password_hash='test-unused', role=role, display_name=role,
                    dept_code=dept, usn=student.usn if role == 'student' else None,
                    faculty_id=fac.id if role in ('faculty', 'hod') else None)
        db.add(user)
        users[role] = user
    users['other_hod'] = User(username=f'{other}.hod', password_hash='test-unused', role='hod', display_name='Other HOD', dept_code=other, faculty_id=outside.id)
    db.add(users['other_hod'])
    term = m.Term(name='Test '+uuid.uuid4().hex, starts_on=dt.date.today()-dt.timedelta(days=14), ends_on=dt.date.today()+dt.timedelta(days=70))
    db.add(term)
    db.flush()
    sec = m.Section(term_id=term.id, dept_code=dept, year=3, semester=5, name='A', size=30)
    foreign_sec = m.Section(term_id=term.id, dept_code=other, year=3, semester=5, name='A', size=30)
    room = m.Room(name='Test room '+uuid.uuid4().hex, dept_code=dept, kind='classroom', capacity=40)
    foreign_room = m.Room(name='Other room '+uuid.uuid4().hex, dept_code=other, kind='classroom', capacity=40)
    db.add_all([sec, foreign_sec, room, foreign_room])
    db.add(m.Qualification(faculty_id=fac.id, subject_code=code))
    db.add(m.FacultyLimit(faculty_id=fac.id, term_id=term.id, daily_limit=4, weekly_limit=20))
    for day in range(5):
        for p in range(4):
            db.add(m.PeriodDefinition(term_id=term.id, day_of_week=day, period_index=p, starts_at=dt.time(9+p), ends_at=dt.time(10+p), is_break=False, is_closed=False))
    db.flush()
    requirement = m.Requirement(section_id=sec.id, assignment_id=assignment.id, periods_per_week=4, max_per_day=1, block_length=1, room_type='classroom', priority=1)
    db.add(requirement)
    db.commit()
    return dict(dept=dept, other=other, faculty=fac, outside=outside, subject=sub, assignment=assignment,
                student=student, users=users, term=term, section=sec, foreign_section=foreign_sec,
                room=room, foreign_room=foreign_room, requirement=requirement)
