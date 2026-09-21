"""Deterministic Library Agent policy and transactions.

Every borrower mutation locks Student, then Book, then the reservation/issue/fine.
This order serializes fine eligibility with collections/expiry/returns and avoids
cycles across books. Service functions stage writes; API/maintenance own commit.
PostgreSQL READ COMMITTED is required for the concurrency guarantee.
"""
import datetime as dt
import re
import secrets
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError

from .. import config
from ..models import (Book, BookDepartment, BookIssue, BookReservation, BookReview,
                      Department, LibraryFine, Student, utcnow)
from ..notifications import notify_usns

INDIA = ZoneInfo('Asia/Kolkata')
ACTIVE_ISSUES = ('ISSUED', 'RETURN_PENDING')

# These aliases are deliberately small and explicit.  They are applied before
# catalogue lookup so common course abbreviations never degrade into a broad
# keyword search (for example, treating "available" as the useful query term).
_DSA_ALIAS = re.compile(
    r"\b(?:dsa|data\s+structure(?:s)?|algorithm(?:s)?|"
    r"data\s+structures?\s+and\s+algorithms?)\b", re.I)
_DSA_CANONICAL = "Data Structures and Algorithms"
_DSA_TERMS = ("data structures", "algorithms", "algorithm analysis", "algorithm")


def canonical_catalogue_term(term: str) -> str:
    """Normalize supported subject aliases without expanding arbitrary input."""
    value = " ".join(str(term or "").split())[:128]
    return _DSA_CANONICAL if _DSA_ALIAS.search(value) else value


def is_dsa_catalogue_term(term: str) -> bool:
    return canonical_catalogue_term(term).casefold() == _DSA_CANONICAL.casefold()


def iso(value):
    return value.replace(tzinfo=dt.timezone.utc).isoformat() if value else None


def overdue(issue, now=None):
    end = issue.returned_at or now or utcnow()
    local = lambda value: value.replace(tzinfo=dt.timezone.utc).astimezone(INDIA).date()
    days = max(0, (local(end) - local(issue.due_at)).days)
    return (Decimal(days) * config.LIBRARY_FINE_PER_OVERDUE_DAY).quantize(Decimal('0.01'))


def locked(db, model, key):
    pk = model.__mapper__.primary_key[0]
    row = db.query(model).filter(pk == key).populate_existing().with_for_update().one_or_none()
    if row is None:
        raise HTTPException(404, 'Library record not found')
    return row


def owned(db, model, key, usn=None):
    query = db.query(model).filter(model.id == key)
    if usn is not None:
        query = query.filter(model.student_usn == usn)
    row = query.one_or_none()
    if row is None:
        raise HTTPException(404, 'Library record not found')
    return row


def workflow_lock(db, model, key, usn=None):
    # Scope ownership before locking/looking up any related private data.
    row = owned(db, model, key, usn)
    locked(db, Student, row.student_usn)
    book = locked(db, Book, row.book_id)
    return locked(db, model, key), book


def unpaid(db, usn):
    return Decimal(db.query(func.coalesce(func.sum(LibraryFine.amount), 0)).filter(
        LibraryFine.student_usn == usn, LibraryFine.status == 'UNPAID').scalar()).quantize(Decimal('0.01'))


def eligible(db, usn):
    if unpaid(db, usn) >= config.LIBRARY_FINE_BLOCK_THRESHOLD:
        raise HTTPException(409, f'Unpaid library fines have reached the ₹{config.LIBRARY_FINE_BLOCK_THRESHOLD} limit. Pay at the library counter.')


def notice(db, usn, event, entity, message):
    notify_usns(db, [usn], title='Library', message=message,
        notification_type=event, source_agent='library_agent', event_key=f'library:{event}:{entity}',
        route='/student/library', related_entity_type='library', related_entity_id=entity)


def add_fine(db, usn, source_type, source_id, amount, reason):
    if amount <= 0:
        return None
    fine = db.query(LibraryFine).filter_by(source_type=source_type, source_id=source_id).one_or_none()
    if fine is None:
        fine = LibraryFine(student_usn=usn, source_type=source_type, source_id=source_id,
                           amount=amount, reason=reason, status='UNPAID')
        db.add(fine); db.flush()
        notice(db, usn, 'fine_created', fine.id, f'{reason}: ₹{amount}. Pay in person at the library.')
    return fine


def book_record(db, book, average=None):
    if average is None:
        average = db.query(func.avg(BookReview.rating)).join(BookIssue, BookIssue.id == BookReview.issue_id).filter(
            BookReview.book_id == book.id, BookIssue.status == 'RETURNED').scalar()
    return {field: getattr(book, field) for field in
            ('id', 'isbn', 'title', 'author', 'publisher', 'category', 'description',
             'total_copies', 'available_copies', 'popularity_count', 'is_active')} | {
        'departments': [code for code, in db.query(BookDepartment.department_code).filter_by(book_id=book.id).order_by(BookDepartment.department_code)],
        'average_rating': round(average, 2) if average is not None else None}


def catalogue(db, q='', offset=0, limit=20, include_archived=False):
    query = db.query(Book)
    if not include_archived:
        query = query.filter(Book.is_active.is_(True))
    if q:
        query = query.filter(or_(*(column.icontains(q, autoescape=True) for column in
                                  (Book.title, Book.author, Book.isbn, Book.category))))
    return {'total': query.count(), 'items': [book_record(db, book) for book in
        query.order_by(func.lower(Book.title), Book.id).offset(offset).limit(limit)]}


def restore_book(db, book_id):
    """Restore an archived catalogue entry without changing its stock or history."""
    book = locked(db, Book, book_id)
    if book.is_active:
        raise HTTPException(409, 'Book is already active')
    book.is_active = True
    db.flush()
    return book


def assistant_catalogue_search(db, term, limit=12, *, available_only=False):
    """Return ranked, active catalogue facts safe for student chat.

    This deliberately has no circulation joins and exposes no database IDs,
    borrower data, reviews, popularity, or mutation capability. Department
    mappings improve discovery only; they never filter student visibility.
    """
    term = canonical_catalogue_term(term)
    if not term:
        return []
    lowered = term.casefold()
    dsa_query = is_dsa_catalogue_term(term)
    browse_all = lowered in {"all books", "all available books"}
    tokens = [token for token in re.findall(r'[a-z0-9+#.]+', lowered)
              if len(token) > 1 and token not in {
                  'the', 'and', 'for', 'book', 'books', 'learning', 'college', 'library', 'all',
                  'data', 'structures', 'algorithms'
              }][:10]
    searchable = (Book.title, Book.author, Book.isbn, Book.category, Book.description)
    if dsa_query:
        # Match the canonical subject only.  In particular, do not turn a DSA
        # request into an unqualified catalogue browse when no match exists.
        clauses = [column.icontains(keyword, autoescape=True)
                   for keyword in _DSA_TERMS for column in searchable]
    elif browse_all:
        clauses = []
    else:
        clauses = [column.icontains(term, autoescape=True) for column in searchable]
        for token in tokens:
            clauses.extend(column.icontains(token, autoescape=True) for column in searchable)
            clauses.append(Book.id.in_(select(BookDepartment.book_id).where(
                BookDepartment.department_code.ilike(token))))
    query = db.query(Book).filter(Book.is_active.is_(True))
    if available_only:
        query = query.filter(Book.available_copies > 0)
    if clauses:
        query = query.filter(or_(*clauses))
    rows = query.limit(60).all()

    def score(book):
        values = {
            'title': (book.title or '').casefold(), 'author': (book.author or '').casefold(),
            'isbn': (book.isbn or '').casefold(), 'category': (book.category or '').casefold(),
            'description': (book.description or '').casefold(),
        }
        if dsa_query:
            subject_values = (values['title'], values['category'], values['description'])
            data_structures = any("data structures" in value for value in subject_values)
            algorithms = any("algorithm" in value for value in subject_values)
            if not (data_structures or algorithms):
                return 0
            # Canonical title/category matches outrank partial subject and
            # author matches, making the top recommendation explainable.
            exact = 1200 if lowered in {values['title'], values['category']} else 0
            title_score = (700 if "data structures and algorithms" in values['title'] else 0)
            title_score += (520 if "data structures" in values['title'] else 0)
            title_score += (420 if "algorithm" in values['title'] else 0)
            category_score = (350 if "data structures" in values['category'] else 0)
            category_score += (260 if "algorithm" in values['category'] else 0)
            keyword_score = (100 if data_structures else 0) + (80 if algorithms else 0)
            return exact + title_score + category_score + keyword_score
        exact = 1000 if lowered in {values['title'], values['isbn']} else 0
        contains = (300 if lowered in values['title'] else 0) + (220 if lowered in values['author'] else 0)
        contains += (180 if lowered in values['category'] else 0) + (80 if lowered in values['description'] else 0)
        coverage = sum(30 for token in tokens if any(token in value for value in values.values()))
        return exact + contains + coverage

    # Generic department matches are intentionally retained: their score may
    # be zero because the department code is held in BookDepartment rather
    # than on Book. Canonical DSA results, however, must always have direct
    # indexed subject evidence.
    if dsa_query:
        rows = [book for book in rows if score(book) > 0]
    rows.sort(key=lambda book: (-score(book), book.title.casefold(), book.id))
    result = []
    for book in rows[:limit]:
        departments = [code for code, in db.query(BookDepartment.department_code).filter_by(
            book_id=book.id).order_by(BookDepartment.department_code)]
        result.append({
            'title': book.title, 'author': book.author, 'isbn': book.isbn,
            'publisher': book.publisher, 'category': book.category,
            'description': book.description, 'departments': departments,
            'total_copies': book.total_copies,
            'available_copies': book.available_copies,
            'availability_status': 'available' if book.available_copies > 0 else 'currently unavailable',
        })
    return result


def reviews(db, book_id=None, offset=0, limit=20):
    query = db.query(BookReview).join(BookIssue, BookIssue.id == BookReview.issue_id).filter(BookIssue.status == 'RETURNED')
    if book_id is not None:
        query = query.filter(BookReview.book_id == book_id)
    return {'total': query.count(), 'items': [{'id': row.id, 'book_id': row.book_id,
        'rating': row.rating, 'comment': row.comment, 'created_at': iso(row.created_at)}
        for row in query.order_by(BookReview.created_at.desc(), BookReview.id.desc()).offset(offset).limit(limit)]}


def save_book(db, body, book_id=None):
    departments = body.departments
    if set(departments) - {code for code, in db.query(Department.code).filter(Department.code.in_(departments))}:
        raise HTTPException(422, 'Unknown department code')
    values = body.model_dump(exclude={'departments'})
    if book_id is None:
        book = Book(**values, available_copies=body.total_copies)
        db.add(book)
    else:
        book = locked(db, Book, book_id)
        committed = book.total_copies - book.available_copies
        if body.total_copies < committed:
            raise HTTPException(409, f'At least {committed} copies are currently issued or held')
        book.available_copies = body.total_copies - committed
        for field, value in values.items():
            setattr(book, field, value)
    # A unique index provides the final guard against simultaneous ISBN edits.
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, 'ISBN already exists or catalogue values conflict') from None
    db.query(BookDepartment).filter_by(book_id=book.id).delete(synchronize_session=False)
    db.add_all([BookDepartment(book_id=book.id, department_code=code) for code in departments])
    db.flush()
    return book


def reserve(db, usn, book_id):
    locked(db, Student, usn)
    book = locked(db, Book, book_id)
    eligible(db, usn)
    if not book.is_active or book.available_copies < 1:
        raise HTTPException(409, 'Book is unavailable')
    if db.query(BookReservation.id).filter_by(student_usn=usn, book_id=book_id, status='PENDING_PICKUP').first():
        raise HTTPException(409, 'You already have a pending reservation for this book')
    now = utcnow()
    # Flush the hold before any savepoint. This also starts a real outer write
    # transaction in SQLite's legacy transaction mode, so a later notification
    # failure cannot leave a savepoint-created reservation committed on its own.
    book.available_copies -= 1
    db.flush()
    # Savepoints retry random-code collisions without losing outer inventory locks.
    for _ in range(50):
        code = f'{secrets.randbelow(1_000_000):06d}'
        if db.query(BookReservation.id).filter_by(slip_code=code).first():
            continue
        try:
            with db.begin_nested():
                row = BookReservation(student_usn=usn, book_id=book_id, slip_code=code,
                    status='PENDING_PICKUP', requested_at=now,
                    pickup_deadline=now + dt.timedelta(days=config.LIBRARY_PICKUP_DEADLINE_DAYS))
                db.add(row); db.flush()
            break
        except IntegrityError:
            continue
    else:
        raise HTTPException(503, 'Could not allocate a slip code; please retry')
    notice(db, usn, 'reserved', row.id, f'{book.title} reserved. Collect by {iso(row.pickup_deadline)}. Open Library for your private slip.')
    return row


def expire_locked(db, row, book, now):
    if row.status != 'PENDING_PICKUP' or row.pickup_deadline > now:
        return False
    row.status = 'EXPIRED'; row.expired_at = now
    book.available_copies += 1
    add_fine(db, row.student_usn, 'MISSED_PICKUP', row.id,
             config.LIBRARY_MISSED_PICKUP_FINE, 'Missed library pickup')
    notice(db, row.student_usn, 'reservation_expired', row.id, f'Reservation for {book.title} expired; the held copy was released.')
    db.flush()
    return True


def cancel(db, usn, reservation_id):
    row, book = workflow_lock(db, BookReservation, reservation_id, usn)
    if row.status == 'CANCELLED':
        return row
    if row.status != 'PENDING_PICKUP':
        raise HTTPException(409, 'Only pending reservations can be cancelled')
    if expire_locked(db, row, book, utcnow()):
        return row
    row.status = 'CANCELLED'; row.cancelled_at = utcnow(); book.available_copies += 1
    db.flush()
    return row


def issue_book(db, usn, book_id, reservation=None):
    locked(db, Student, usn)
    book = locked(db, Book, book_id)
    eligible(db, usn)
    if reservation is None:
        if not book.is_active or book.available_copies < 1:
            raise HTTPException(409, 'Book is unavailable')
        book.available_copies -= 1
    now = utcnow()
    issue = BookIssue(student_usn=usn, book_id=book_id, reservation_id=reservation.id if reservation else None,
        status='ISSUED', issued_at=now, due_at=now + dt.timedelta(days=config.LIBRARY_LOAN_DAYS))
    db.add(issue); book.popularity_count += 1; db.flush()
    notice(db, usn, 'issued', issue.id, f'{book.title} issued. Due {iso(issue.due_at)}.')
    return issue


def pickup(db, reservation_id):
    row, book = workflow_lock(db, BookReservation, reservation_id)
    if row.status == 'COLLECTED':
        return db.query(BookIssue).filter_by(reservation_id=row.id).one()
    if row.status != 'PENDING_PICKUP' or row.pickup_deadline <= utcnow():
        raise HTTPException(409, 'Reservation is not valid for pickup')
    if book.total_copies - book.available_copies < 1:
        raise HTTPException(409, 'Held inventory is inconsistent')
    issue = issue_book(db, row.student_usn, row.book_id, row)
    row.status = 'COLLECTED'; row.collected_at = issue.issued_at
    db.flush()
    return issue


def request_return(db, usn, issue_id, body):
    row, _ = workflow_lock(db, BookIssue, issue_id, usn)
    if row.status != 'ISSUED':
        raise HTTPException(409, 'Only issued books can request return')
    row.status = 'RETURN_PENDING'; row.return_requested_at = utcnow(); row.return_rejection_reason = None
    if body.rating is not None:
        review = db.query(BookReview).filter_by(issue_id=row.id).one_or_none()
        if review is None:
            review = BookReview(student_usn=usn, book_id=row.book_id, issue_id=row.id)
            db.add(review)
        review.rating = body.rating; review.comment = body.comment
    db.flush()
    return row


def confirm_return(db, issue_id):
    row, book = workflow_lock(db, BookIssue, issue_id)
    if row.status == 'RETURNED':
        return row
    row.returned_at = utcnow(); row.status = 'RETURNED'; book.available_copies += 1
    add_fine(db, row.student_usn, 'OVERDUE_RETURN', row.id, overdue(row), 'Overdue library return')
    notice(db, row.student_usn, 'return_confirmed', row.id, f'Physical return of {book.title} confirmed.')
    db.flush()
    return row


def reject_return(db, issue_id, reason):
    row, _ = workflow_lock(db, BookIssue, issue_id)
    if row.status != 'RETURN_PENDING':
        raise HTTPException(409, 'No pending return request')
    row.status = 'ISSUED'; row.return_rejection_reason = reason
    db.flush()
    return row


def pay_fine(db, fine_id, collector_id):
    fine = owned(db, LibraryFine, fine_id)
    locked(db, Student, fine.student_usn)
    fine = locked(db, LibraryFine, fine_id)
    if fine.status != 'PAID':
        fine.status = 'PAID'; fine.paid_at = utcnow(); fine.collected_by_user_id = collector_id
        notice(db, fine.student_usn, 'fine_paid', fine.id, f'Library fine of ₹{fine.amount} collected in person.')
    db.flush()
    return fine


def reservation_record(db, row, slip=False):
    book = db.get(Book, row.book_id)
    result = {field: getattr(row, field) for field in ('id', 'book_id', 'student_usn', 'status')}
    result.update(title=book.title, author=book.author, requested_at=iso(row.requested_at), pickup_deadline=iso(row.pickup_deadline))
    if slip:
        result.update(slip_code=row.slip_code, student_name=db.get(Student, row.student_usn).name)
    return result


def issue_record(db, row, now=None):
    result = {field: getattr(row, field) for field in ('id', 'book_id', 'student_usn', 'status', 'return_rejection_reason')}
    result.update(title=db.get(Book, row.book_id).title, issued_at=iso(row.issued_at), due_at=iso(row.due_at),
        return_requested_at=iso(row.return_requested_at), returned_at=iso(row.returned_at),
        estimated_fine=str(overdue(row, now) if row.status != 'RETURNED' else Decimal('0.00')))
    return result


def fine_record(row):
    return {field: getattr(row, field) for field in ('id', 'student_usn', 'source_type', 'source_id', 'reason', 'status')} | {
        'amount': str(row.amount), 'created_at': iso(row.created_at), 'paid_at': iso(row.paid_at)}


def summary(db, usn, parent=False):
    now = utcnow()
    issues = db.query(BookIssue).filter(BookIssue.student_usn == usn, BookIssue.status.in_(ACTIVE_ISSUES)).order_by(BookIssue.due_at, BookIssue.id).all()
    totals = dict(db.query(LibraryFine.status, func.sum(LibraryFine.amount)).filter_by(student_usn=usn).group_by(LibraryFine.status).all())
    borrowed = [issue_record(db, row, now) for row in issues]
    if parent:
        borrowed = [{key: row[key] for key in ('title', 'due_at', 'status', 'estimated_fine')} for row in borrowed]
    return {'issued_count': len(issues), 'borrowed': borrowed,
        'next_due_at': iso(issues[0].due_at) if issues else None,
        'estimated_overdue_total': str(sum((overdue(row, now) for row in issues), Decimal('0.00'))),
        'unpaid_total': str(totals.get('UNPAID', Decimal('0.00'))),
        'paid_total': str(totals.get('PAID', Decimal('0.00'))),
        'fine_block_threshold': str(config.LIBRARY_FINE_BLOCK_THRESHOLD)}


def recommendations(db, usn):
    student = db.get(Student, usn)
    reserved = select(BookReservation.book_id).where(BookReservation.student_usn == usn, BookReservation.status == 'PENDING_PICKUP')
    borrowed = select(BookIssue.book_id).where(BookIssue.student_usn == usn, BookIssue.status.in_(ACTIVE_ISSUES))
    history = select(Book.category).join(BookIssue, BookIssue.book_id == Book.id).where(BookIssue.student_usn == usn)
    relevant = select(BookDepartment.book_id).where(BookDepartment.department_code == student.dept_code)
    ratings = select(func.avg(BookReview.rating)).join(BookIssue, BookIssue.id == BookReview.issue_id).where(
        BookReview.book_id == Book.id, BookIssue.status == 'RETURNED').correlate(Book).scalar_subquery()
    score = (case((Book.category.in_(history), 5), else_=0) + case((Book.id.in_(relevant), 4), else_=0)
             + case((ratings >= 4, 3), else_=0) + case((Book.available_copies > 0, 2), else_=0)
             + case((Book.popularity_count >= 10, 1), else_=0))
    rows = db.query(Book, ratings).filter(Book.is_active.is_(True), ~Book.id.in_(reserved), ~Book.id.in_(borrowed)).order_by(
        score.desc(), func.lower(Book.title), Book.id).limit(config.LIBRARY_RECOMMENDATION_LIMIT).all()
    categories = set(db.scalars(history)); department_books = set(db.scalars(relevant))
    result = []
    for book, average in rows:
        reasons = []
        if book.category in categories: reasons.append('Matches categories you have borrowed')
        if book.id in department_books: reasons.append('Relevant to your department')
        if average is not None and average >= 4: reasons.append('Highly rated by readers')
        if book.available_copies > 0: reasons.append('Available for pickup')
        if book.popularity_count >= 10: reasons.append('Popular with readers')
        result.append(book_record(db, book, average) | {'explanations': reasons or ['Explore a new category']})
    return {'items': result}


def expire_batch(db, limit=200):
    now = utcnow()
    ids = [value for value, in db.query(BookReservation.id).filter(
        BookReservation.status == 'PENDING_PICKUP', BookReservation.pickup_deadline <= now).order_by(BookReservation.id).limit(limit)]
    count = 0
    # Commit one reservation at a time; no cross-student lock accumulation.
    for key in ids:
        try:
            row, book = workflow_lock(db, BookReservation, key)
            count += int(expire_locked(db, row, book, now))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return count
