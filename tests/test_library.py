"""Library authorization, physical workflows, inventory and fine policy."""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import create_token, hash_password
from backend.app.main import app
from backend.app.models import (Book, BookDepartment, BookIssue, BookReservation, BookReview,
    Department, LibraryFine, LibrarianAccount, Notification, Parent, ParentStudent, Student, User)
from backend.app.library import service as s
from backend.app.library.api import slip_pdf
from backend.app.library.schemas import BookInput

client = TestClient(app)


def headers(user):
    return {'Authorization': f'Bearer {create_token(user)}'}


@pytest.fixture
def library(db):
    tag = uuid.uuid4().hex[:8]
    users = {}
    for role in ('student', 'other', 'librarian', 'admin', 'faculty', 'hod', 'principal', 'parent'):
        usn = None
        if role in ('student', 'other'):
            usn = f'LIB{tag}{role[0]}'.upper()
            db.add(Student(usn=usn, name=role, dept_code='AIML', year=3, semester=5, cgpa=8))
            db.flush()
        user = User(username=f'lib.{role}.{tag}', password_hash=hash_password('Original-123!'),
                    role='student' if role == 'other' else role, display_name=role, usn=usn)
        db.add(user); db.flush(); users[role] = user
        if role == 'librarian': db.add(LibrarianAccount(user_id=user.id))
        if role == 'parent':
            parent = Parent(user_id=user.id, full_name='Library Parent'); db.add(parent); db.flush()
            db.add(ParentStudent(parent_id=parent.id, student_usn=users['student'].usn, relationship='Guardian'))
    book = Book(isbn=str(uuid.uuid4().int)[:13], title=f'Library {tag}', author='Test Author',
                category='Computing', total_copies=2, available_copies=2)
    db.add(book); db.commit()
    return users, book


def call(users, role, method, path, body=None):
    return getattr(client, method)('/api' + path, headers=headers(users[role]), **({'json': body} if body is not None else {}))


def reserve(users, book, role='student'):
    response = call(users, role, 'post', '/student/library/reservations', {'book_id': book.id})
    assert response.status_code == 201, response.text
    return response.json()


def issue(users, book, role='student'):
    result = call(users, 'librarian', 'post', '/librarian/library/issues', {'book_id': book.id, 'student_usn': users[role].usn})
    assert result.status_code == 200, result.text
    return result.json()


def test_reservation_hold_duplicate_cancellation_and_owned_slip(db, library):
    users, book = library; row = reserve(users, book)
    db.expire_all(); assert db.get(Book, book.id).available_copies == 1
    duplicate = call(users, 'student', 'post', '/student/library/reservations', {'book_id': book.id})
    assert duplicate.status_code == 409
    for suffix in ('slip', 'slip.pdf'):
        assert call(users, 'other', 'get', f"/student/library/reservations/{row['id']}/{suffix}").status_code == 404
    response = call(users, 'student', 'get', f"/student/library/reservations/{row['id']}/slip")
    assert response.headers['cache-control'] == 'no-store'
    assert len(response.json()['slip_code']) == 6
    pdf = call(users, 'student', 'get', f"/student/library/reservations/{row['id']}/slip.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-1.4')
    assert pdf.headers['cache-control'] == 'no-store'
    assert pdf.headers['content-disposition'] == f"attachment; filename=\"library-slip-{row['id']}.pdf\""
    # The printable document retains every former slip field and adds the
    # reader-facing book/student details required at the verification desk.
    for value in (b'BOOK VERIFICATION SLIP', str(row['id']).encode(), row['student_usn'].encode(),
                  book.isbn.encode(), response.json()['slip_code'].encode(), b'PENDING_PICKUP',
                  book.title.encode(), book.author.encode(), b'VERIFICATION STATUS'):
        assert value in pdf.content
    assert 'slip_code' not in str(call(users, 'student', 'get', '/student/library/reservations').json())
    assert call(users, 'other', 'post', f"/student/library/reservations/{row['id']}/cancel").status_code == 404
    for _ in range(2):
        assert call(users, 'student', 'post', f"/student/library/reservations/{row['id']}/cancel").status_code == 200
    db.expire_all(); assert db.get(Book, book.id).available_copies == 2


def test_verification_pdf_wraps_maximum_catalogue_fields_on_one_a4_page():
    """The generator's bounds guard and wrapping keep long catalogue data printable."""
    title, author = 'Start ' + 'title ' * 50 + 'Finish', 'First ' + 'author ' * 35 + 'Last'
    pdf = slip_pdf({
        'reservation_id': 999, 'slip_code': '012345', 'requested_at': '01 Aug 2026 10:00 IST',
        'student_name': 'Student Name ' * 10, 'student_usn': '4MT23AI001',
        'programme': 'Artificial Intelligence and Machine Learning', 'semester': 'Semester 5 / Year 3',
        'title': title, 'author': author, 'isbn': '9780000000001', 'book_id': 101,
        'pickup_deadline': '03 Aug 2026 10:00 IST', 'status': 'PENDING_PICKUP',
    })
    assert b'/MediaBox [0 0 595 842]' in pdf  # A4, one page
    assert b'/Count 1' in pdf
    # The first and last words prove wrapped values are not clipped at either end.
    assert b'(Start ' in pdf and b'Finish)' in pdf
    assert b'(First ' in pdf and b'Last)' in pdf
    assert b'Page 1 of 1' in pdf


def test_pickup_safe_retries_and_return_fines_until_physical_confirmation(db, library, monkeypatch):
    users, book = library
    now = dt.datetime(2026, 8, 1, 10)
    monkeypatch.setattr(s, 'utcnow', lambda: now)
    reserved = reserve(users, book)
    first = call(users, 'librarian', 'post', f"/librarian/library/reservations/{reserved['id']}/pickup")
    assert first.status_code == 200, first.text
    issued = first.json(); assert issued['due_at'] == '2026-08-08T10:00:00+00:00'
    for _ in range(2):
        assert call(users, 'admin', 'post', f"/librarian/library/reservations/{reserved['id']}/pickup").json()['id'] == issued['id']
    db.expire_all(); assert db.get(Book, book.id).available_copies == 1
    now = dt.datetime(2026, 8, 9, 10)
    path = f"/student/library/issues/{issued['id']}/return-request"
    assert call(users, 'other', 'post', path, {}).status_code == 404
    assert call(users, 'student', 'post', path, {'rating': 6}).status_code == 422
    assert call(users, 'student', 'post', path, {'comment': 'No rating'}).status_code == 422
    assert call(users, 'student', 'post', path, {'rating': 5, 'comment': '<script>alert(1)</script>'}).status_code == 200
    assert call(users, 'student', 'get', '/student/library/summary').json()['estimated_overdue_total'] == '1.00'
    assert call(users, 'student', 'get', f'/library/books/{book.id}/reviews').json()['items'] == []
    now = dt.datetime(2026, 8, 13, 10)
    summary = call(users, 'student', 'get', '/student/library/summary').json()
    assert summary['estimated_overdue_total'] == '5.00' and summary['unpaid_total'] == '0.00'
    parent = call(users, 'parent', 'get', f"/parent/children/{users['student'].usn}/library").json()
    assert parent['estimated_overdue_total'] == '5.00'
    assert not {'id', 'student_usn', 'book_id'} & parent['borrowed'][0].keys()
    for _ in range(2):
        assert call(users, 'librarian', 'post', f"/librarian/library/issues/{issued['id']}/return").status_code == 200
    db.expire_all(); assert db.get(Book, book.id).available_copies == 2
    fines = db.query(LibraryFine).filter_by(source_type='OVERDUE_RETURN', source_id=issued['id']).all()
    assert len(fines) == 1 and fines[0].amount == Decimal('5.00')
    summary = call(users, 'student', 'get', '/student/library/summary').json()
    assert summary['estimated_overdue_total'] == '0.00' and summary['unpaid_total'] == '5.00'
    review = call(users, 'student', 'get', f'/library/books/{book.id}/reviews').json()['items'][0]
    assert review['comment'] == '<script>alert(1)</script>' and 'student_usn' not in review
    assert call(users, 'student', 'get', f'/library/books/{book.id}').json()['average_rating'] == 5


def test_rejected_return_fine_keeps_growing(db, library, monkeypatch):
    users, book = library; issued = issue(users, book)
    row = db.get(BookIssue, issued['id']); row.due_at = dt.datetime(2026, 8, 8, 10); row.issued_at = row.due_at - dt.timedelta(days=7); db.commit()
    assert call(users, 'student', 'post', f"/student/library/issues/{row.id}/return-request", {'rating': 4}).status_code == 200
    assert call(users, 'librarian', 'post', f'/librarian/library/issues/{row.id}/reject-return', {'reason': '  '}).status_code == 422
    assert call(users, 'librarian', 'post', f'/librarian/library/issues/{row.id}/reject-return', {'reason': 'Book not handed over'}).status_code == 200
    monkeypatch.setattr(s, 'utcnow', lambda: dt.datetime(2026, 8, 10, 10))
    result = call(users, 'student', 'get', '/student/library/issues').json()['items'][0]
    assert result['status'] == 'ISSUED' and result['estimated_fine'] == '2.00'
    assert call(users, 'student', 'post', f'/student/library/issues/{row.id}/return-request', {'rating': 3}).status_code == 200
    assert db.query(BookReview).filter_by(issue_id=row.id).count() == 1


def test_expiry_release_and_fine_once_including_late_cancel(db, library, monkeypatch):
    users, book = library; row = reserve(users, book)
    reservation = db.get(BookReservation, row['id']); reservation.requested_at = dt.datetime(2026, 8, 1); reservation.pickup_deadline = dt.datetime(2026, 8, 3); db.commit()
    monkeypatch.setattr(s, 'utcnow', lambda: dt.datetime(2026, 8, 4))
    assert call(users, 'librarian', 'post', f"/librarian/library/reservations/{row['id']}/pickup").status_code == 409
    assert s.expire_batch(db) >= 1
    assert s.expire_batch(db) == 0
    db.expire_all(); assert db.get(Book, book.id).available_copies == 2
    assert db.query(LibraryFine).filter_by(source_type='MISSED_PICKUP', source_id=row['id']).one().amount == Decimal('10')
    another = reserve(users, book); reservation = db.get(BookReservation, another['id']); reservation.requested_at = dt.datetime(2026, 8, 1); reservation.pickup_deadline = dt.datetime(2026, 8, 3); db.commit()
    assert call(users, 'student', 'post', f"/student/library/reservations/{another['id']}/cancel").json()['status'] == 'EXPIRED'
    db.expire_all(); assert db.get(Book, book.id).available_copies == 2


def test_threshold_payment_unblocks_and_notifications_owned_deduplicated(db, library):
    users, book = library
    fine = LibraryFine(student_usn=users['student'].usn, source_type='MISSED_PICKUP', source_id=999999,
                       amount=Decimal('25'), reason='Prior missed pickups', status='UNPAID')
    db.add(fine); db.commit()
    assert call(users, 'student', 'post', '/student/library/reservations', {'book_id': book.id}).status_code == 409
    assert call(users, 'librarian', 'post', '/librarian/library/issues', {'book_id': book.id, 'student_usn': users['student'].usn}).status_code == 409
    assert call(users, 'student', 'post', f'/librarian/library/fines/{fine.id}/paid').status_code == 403
    for _ in range(2): assert call(users, 'librarian', 'post', f'/librarian/library/fines/{fine.id}/paid').status_code == 200
    reserved = reserve(users, book)
    notices = db.query(Notification).filter(Notification.event_key.in_([f'library:reserved:{reserved["id"]}', f'library:fine_paid:{fine.id}'])).all()
    assert len(notices) == 2
    assert all(row.recipient_user_id == users['student'].id and row.route == '/student/library' for row in notices)
    db.expire_all(); assert db.get(LibraryFine, fine.id).collected_by_user_id == users['librarian'].id
    assert call(users, 'other', 'get', '/student/library/fines').json()['items'] == []


def test_stock_archive_and_recommendations(db, library):
    users, book = library; reserved = reserve(users, book)
    payload = BookInput(isbn=book.isbn, title=book.title, author=book.author, category=book.category,
                        total_copies=0, departments=['aiml']).model_dump()
    assert call(users, 'librarian', 'put', f'/librarian/library/books/{book.id}', payload).status_code == 409
    payload['available_copies'] = 3
    assert call(users, 'librarian', 'put', f'/librarian/library/books/{book.id}', payload).status_code == 422
    payload.pop('available_copies'); payload['total_copies'] = 3
    result = call(users, 'librarian', 'put', f'/librarian/library/books/{book.id}', payload)
    assert result.status_code == 200, result.text
    assert result.json()['available_copies'] == 2 and result.json()['departments'] == ['AIML']
    assert book.id not in [row['id'] for row in call(users, 'student', 'get', '/student/library/recommendations').json()['items']]
    assert call(users, 'admin', 'post', f'/librarian/library/books/{book.id}/archive').status_code == 200
    assert call(users, 'other', 'post', '/student/library/reservations', {'book_id': book.id}).status_code == 409
    # Existing holds may still be honoured, but archived stock cannot be newly reserved.
    pickup = call(users, 'librarian', 'post', f"/librarian/library/reservations/{reserved['id']}/pickup")
    assert pickup.status_code == 200
    assert call(users, 'student', 'get', '/student/library/issues').json()['items'][0]['title'] == book.title
    assert call(users, 'student', 'get', '/library/books?limit=101').status_code == 422


def test_restore_archived_book_preserves_stock_and_history_and_requires_staff(db, library):
    users, book = library
    issued = issue(users, book)
    db.expire_all(); book = db.get(Book, book.id)
    original = (book.isbn, book.title, book.author, book.category, book.total_copies, book.available_copies)
    assert call(users, 'librarian', 'post', f'/librarian/library/books/{book.id}/archive').status_code == 200
    assert call(users, 'student', 'post', f'/librarian/library/books/{book.id}/unarchive').status_code == 403
    restored = call(users, 'admin', 'post', f'/librarian/library/books/{book.id}/unarchive')
    assert restored.status_code == 200, restored.text
    assert restored.json()['is_active'] is True
    db.expire_all(); persisted = db.get(Book, book.id)
    assert (persisted.isbn, persisted.title, persisted.author, persisted.category,
            persisted.total_copies, persisted.available_copies) == original
    assert db.get(BookIssue, issued['id']).book_id == book.id
    assert call(users, 'librarian', 'post', f'/librarian/library/books/{book.id}/unarchive').status_code == 409
    # Restored entries become eligible again; the existing circulation history remains intact.
    assert reserve(users, book, role='other')['book_id'] == book.id


@pytest.mark.parametrize('role', ['student', 'other', 'faculty', 'hod', 'principal', 'parent'])
def test_non_librarian_roles_cannot_read_or_mutate_operations(library, role):
    users, book = library
    for path in ['/librarian/library/summary', '/librarian/library/records/issues', '/librarian/library/records/fines', '/admin/librarians']:
        assert call(users, role, 'get', path).status_code == 403
    for path in ['/librarian/library/verify-slip', '/librarian/library/issues', '/librarian/library/reservations/1/pickup',
                 '/librarian/library/issues/1/return', '/librarian/library/issues/1/reject-return', '/librarian/library/fines/1/paid',
                 '/librarian/library/books', '/librarian/library/books/1/archive', '/librarian/library/books/1/unarchive', '/admin/librarians']:
        assert call(users, role, 'post', path, {}).status_code == 403
    assert call(users, role, 'put', f'/librarian/library/books/{book.id}', {}).status_code == 403
    assert call(users, role, 'get', '/library/books?include_archived=true').status_code == 403


def test_parent_links_no_slips_and_staff_cannot_impersonate(library):
    users, book = library; reserve(users, book)
    assert call(users, 'parent', 'get', f"/parent/children/{users['other'].usn}/library").status_code == 403
    assert call(users, 'parent', 'get', f"/parent/children/{users['student'].usn}/library").status_code == 200
    for role in ('parent', 'librarian', 'admin', 'hod', 'faculty', 'principal'):
        for path in ['/student/library/summary', '/student/library/reservations', '/student/library/issues', '/student/library/fines', '/student/library/recommendations', '/student/library/reservations/1/slip']:
            assert call(users, role, 'get', path).status_code == 403
        assert call(users, role, 'post', '/student/library/reservations', {'book_id': book.id}).status_code == 403
    assert client.get('/api/library/books').status_code == 401


def test_librarian_account_creation_password_change_and_deactivation(db, library):
    users, _ = library
    result = call(users, 'admin', 'post', '/admin/librarians', {'username': f'new.lib.{uuid.uuid4().hex[:8]}', 'display_name': 'New Librarian'})
    assert result.status_code == 201, result.text
    account = result.json(); creds = account['generated_credentials']
    user = db.get(User, account['id']); assert user.password_hash != creds['temporary_password']
    assert call(users, 'librarian', 'post', '/admin/librarians', {}).status_code == 403
    auth = headers(user)
    assert client.get('/api/librarian/library/summary', headers=auth).status_code == 403
    assert client.post('/api/auth/change-password', headers=auth, json={'current_password': creds['temporary_password'], 'new_password': 'Changed-password-123!'}).status_code == 200
    assert client.get('/api/librarian/library/summary', headers=auth).status_code == 200
    assert call(users, 'admin', 'put', f"/admin/librarians/{user.id}", {'display_name': 'Renamed', 'active': False}).status_code == 200
    assert client.get('/api/librarian/library/summary', headers=auth).status_code == 403
    assert client.post('/api/auth/login', json={'username': user.username, 'password': 'Changed-password-123!'}).status_code == 403
    listing = call(users, 'admin', 'get', '/admin/librarians').json()
    assert 'password_hash' not in str(listing) and creds['temporary_password'] not in str(listing)


def test_calendar_day_fine_boundary_uses_india():
    row = BookIssue(due_at=dt.datetime(2026, 8, 8, 18, 29), status='ISSUED')
    assert s.overdue(row, dt.datetime(2026, 8, 8, 18, 29, 59)) == Decimal('0')
    assert s.overdue(row, dt.datetime(2026, 8, 8, 18, 30)) == Decimal('1')


def test_new_fines_block_existing_pickup_without_losing_held_copy(db, library):
    users, book = library; reserved = reserve(users, book)
    fine = LibraryFine(student_usn=users['student'].usn, source_type='MISSED_PICKUP', source_id=reserved['id'],
                       amount=Decimal('25'), reason='Prior debt', status='UNPAID')
    db.add(fine); db.commit()
    path = f"/librarian/library/reservations/{reserved['id']}/pickup"
    assert call(users, 'librarian', 'post', path).status_code == 409
    db.expire_all(); assert db.get(Book, book.id).available_copies == 1
    assert db.get(BookReservation, reserved['id']).status == 'PENDING_PICKUP'
    assert call(users, 'librarian', 'post', f'/librarian/library/fines/{fine.id}/paid').status_code == 200
    assert call(users, 'librarian', 'post', path).status_code == 200


def test_notifications_roll_back_with_inventory_and_reservation(db, library, monkeypatch):
    users, book = library
    def unavailable(*args, **kwargs):
        raise RuntimeError('Notification persistence failed')
    monkeypatch.setattr(s, 'notice', unavailable)
    with pytest.raises(RuntimeError):
        s.reserve(db, users['student'].usn, book.id)
    db.rollback()
    assert db.get(Book, book.id).available_copies == 2
    assert db.query(BookReservation).filter_by(book_id=book.id).count() == 0


def test_recommendations_explain_all_factors_and_search_escapes_wildcards(db, library):
    users, book = library
    book.category = 'Unique-' + uuid.uuid4().hex[:8]; book.popularity_count = 11
    db.add(BookDepartment(book_id=book.id, department_code='AIML'))
    now = s.utcnow()
    old = BookIssue(student_usn=users['student'].usn, book_id=book.id, status='RETURNED',
                    issued_at=now - dt.timedelta(days=8), due_at=now - dt.timedelta(days=1), returned_at=now)
    db.add(old); db.flush()
    db.add(BookReview(student_usn=users['student'].usn, book_id=book.id, issue_id=old.id, rating=5, comment='Useful'))
    db.commit()
    recs = call(users, 'student', 'get', '/student/library/recommendations').json()['items']
    item = next(row for row in recs if row['id'] == book.id)
    assert len(item['explanations']) == 5
    assert call(users, 'student', 'get', '/library/books?q=%25').json()['items'] == []
    result = call(users, 'student', 'get', f'/library/books?q={book.isbn}&limit=1').json()
    assert result['total'] == 1 and result['items'][0]['id'] == book.id


def test_normalized_isbn_uniqueness_and_negative_stock(db, library):
    users, book = library
    payload = {'isbn': book.isbn[:3] + '-' + book.isbn[3:], 'title': 'Duplicate ISBN', 'author': 'Author',
               'category': 'Test', 'total_copies': 1, 'departments': []}
    assert call(users, 'admin', 'post', '/librarian/library/books', payload).status_code == 409
    payload['total_copies'] = -1
    assert call(users, 'admin', 'post', '/librarian/library/books', payload).status_code == 422
    assert call(users, 'admin', 'delete', f'/librarian/library/books/{book.id}').status_code == 405
