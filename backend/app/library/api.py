"""Authenticated physical library API; no public or parent mutations."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..agents import get_agents
from ..auth import get_current_user, hash_password, require_role
from ..database import get_session
from ..models import (Book, BookIssue, BookReservation, LibraryFine, LibrarianAccount,
                      Student, User, utcnow)
from ..parent_portal import child_for_parent, generated_password, require_parent
from . import service as s
from .schemas import (BookInput, DirectIssueInput, LibrarianCreate, LibrarianUpdate,
                      RejectInput, ReserveInput, ReturnInput, SlipInput)

router = APIRouter(prefix='/api', tags=['library'])
student = require_role('student')
staff = require_role('librarian', 'admin')
admin = require_role('admin')


def student_usn(user):
    if not user.usn:
        raise HTTPException(403, 'Student identity is not linked')
    return user.usn


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Library data changed; refresh and retry') from None


@router.get('/library/books')
def books(q: str = Query('', max_length=128), offset: int = Query(0, ge=0),
          limit: int = Query(20, ge=1, le=100), include_archived: bool = False,
          user=Depends(get_current_user), db=Depends(get_session)):
    if include_archived and user.role not in ('librarian', 'admin'):
        raise HTTPException(403, 'Catalogue management role required')
    return s.catalogue(db, q, offset, limit, include_archived)


@router.get('/library/books/{book_id}')
def book_detail(book_id: int, user=Depends(get_current_user), db=Depends(get_session)):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(404, 'Book not found')
    return s.book_record(db, book)


@router.get('/library/books/{book_id}/reviews')
def book_reviews(book_id: int, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                 user=Depends(get_current_user), db=Depends(get_session)):
    return s.reviews(db, book_id, offset, limit)


@router.post('/librarian/library/books', status_code=201)
def create_book(body: BookInput, user=Depends(staff), db=Depends(get_session)):
    book = get_agents()['library_agent'].save_book(db, body); commit(db)
    return s.book_record(db, book)


@router.put('/librarian/library/books/{book_id}')
def update_book(book_id: int, body: BookInput, user=Depends(staff), db=Depends(get_session)):
    book = get_agents()['library_agent'].save_book(db, body, book_id); commit(db)
    return s.book_record(db, book)


@router.post('/librarian/library/books/{book_id}/archive')
def archive_book(book_id: int, user=Depends(staff), db=Depends(get_session)):
    book = s.locked(db, Book, book_id); book.is_active = False; commit(db)
    return s.book_record(db, book)


@router.get('/student/library/summary')
def student_summary(user=Depends(student), db=Depends(get_session)):
    return s.summary(db, student_usn(user))


@router.get('/student/library/reservations')
def reservations(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                 user=Depends(student), db=Depends(get_session)):
    query = db.query(BookReservation).filter_by(student_usn=student_usn(user))
    return {'total': query.count(), 'items': [s.reservation_record(db, row) for row in
        query.order_by(BookReservation.id.desc()).offset(offset).limit(limit)]}


@router.post('/student/library/reservations', status_code=201)
def reserve(body: ReserveInput, user=Depends(student), db=Depends(get_session)):
    row = get_agents()['library_agent'].reserve(db, student_usn(user), body.book_id); commit(db)
    return s.reservation_record(db, row)


@router.post('/student/library/reservations/{reservation_id}/cancel')
def cancel(reservation_id: int, user=Depends(student), db=Depends(get_session)):
    row = get_agents()['library_agent'].cancel(db, student_usn(user), reservation_id); commit(db)
    return s.reservation_record(db, row)


@router.get('/student/library/reservations/{reservation_id}/slip')
def slip(reservation_id: int, response: Response, user=Depends(student), db=Depends(get_session)):
    response.headers['Cache-Control'] = 'no-store'
    return s.reservation_record(db, s.owned(db, BookReservation, reservation_id, student_usn(user)), slip=True)


def slip_pdf(lines):
    """Small single-page PDF using only ASCII identifiers and built-in Helvetica."""
    escape = lambda text: str(text).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    content = ('BT /F1 14 Tf 50 780 Td ' + ' '.join(f'({escape(line)}) Tj 0 -28 Td' for line in lines) + ' ET').encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        f'<< /Length {len(content)} >>\nstream\n'.encode() + content + b'\nendstream']
    data = b'%PDF-1.4\n'; offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return data + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()


@router.get('/student/library/reservations/{reservation_id}/slip.pdf')
def pdf(reservation_id: int, user=Depends(student), db=Depends(get_session)):
    row = s.owned(db, BookReservation, reservation_id, student_usn(user))
    # Encode identifiers safely even if a legacy USN contains non-ASCII text.
    usn = row.student_usn.encode('ascii', 'backslashreplace').decode()
    deadline = row.pickup_deadline.replace(tzinfo=dt.timezone.utc).astimezone(s.INDIA).strftime('%d %b %Y %H:%M IST')
    data = slip_pdf(['MAWOS Library - Pickup acknowledgement', f'Reservation: {row.id}',
        f'Student USN: {usn}', f'Book ISBN: {db.get(Book, row.book_id).isbn}',
        f'Pickup code: {row.slip_code}', f'Status: {row.status}', f'Collect by: {deadline}',
        'Bring this slip and your student ID to the library.', 'Validity is checked at physical handover.'])
    return Response(data, media_type='application/pdf', headers={'Cache-Control': 'no-store',
        'Content-Disposition': f'attachment; filename="library-slip-{row.id}.pdf"'})


@router.get('/student/library/issues')
def issues(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
           user=Depends(student), db=Depends(get_session)):
    query = db.query(BookIssue).filter_by(student_usn=student_usn(user))
    return {'total': query.count(), 'items': [s.issue_record(db, row) for row in
        query.order_by(BookIssue.id.desc()).offset(offset).limit(limit)]}


@router.post('/student/library/issues/{issue_id}/return-request')
def request_return(issue_id: int, body: ReturnInput, user=Depends(student), db=Depends(get_session)):
    row = get_agents()['library_agent'].request_return(db, student_usn(user), issue_id, body); commit(db)
    return s.issue_record(db, row)


@router.get('/student/library/fines')
def fines(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
          user=Depends(student), db=Depends(get_session)):
    query = db.query(LibraryFine).filter_by(student_usn=student_usn(user))
    return {'total': query.count(), 'items': [s.fine_record(row) for row in
        query.order_by(LibraryFine.id.desc()).offset(offset).limit(limit)]}


@router.get('/student/library/recommendations')
def recommendations(user=Depends(student), db=Depends(get_session)):
    return get_agents()['library_agent'].recommendations(db, student_usn(user))


@router.post('/librarian/library/verify-slip')
def verify(body: SlipInput, response: Response, user=Depends(staff), db=Depends(get_session)):
    response.headers['Cache-Control'] = 'no-store'
    row = db.query(BookReservation).filter_by(slip_code=body.slip_code, status='PENDING_PICKUP').filter(
        BookReservation.pickup_deadline > utcnow()).one_or_none()
    if row is None:
        raise HTTPException(404, 'No valid pickup for this code')
    return s.reservation_record(db, row, slip=True)


@router.post('/librarian/library/reservations/{reservation_id}/pickup')
def pickup(reservation_id: int, user=Depends(staff), db=Depends(get_session)):
    row = get_agents()['library_agent'].pickup(db, reservation_id); commit(db)
    return s.issue_record(db, row)


@router.post('/librarian/library/issues')
def direct_issue(body: DirectIssueInput, user=Depends(staff), db=Depends(get_session)):
    row = get_agents()['library_agent'].issue_book(db, body.student_usn, body.book_id); commit(db)
    return s.issue_record(db, row)


@router.post('/librarian/library/issues/{issue_id}/return')
def physical_return(issue_id: int, user=Depends(staff), db=Depends(get_session)):
    row = get_agents()['library_agent'].confirm_return(db, issue_id); commit(db)
    return s.issue_record(db, row)


@router.post('/librarian/library/issues/{issue_id}/reject-return')
def reject_return(issue_id: int, body: RejectInput, user=Depends(staff), db=Depends(get_session)):
    row = get_agents()['library_agent'].reject_return(db, issue_id, body.reason); commit(db)
    return s.issue_record(db, row)


@router.post('/librarian/library/fines/{fine_id}/paid')
def pay(fine_id: int, user=Depends(staff), db=Depends(get_session)):
    row = get_agents()['library_agent'].pay_fine(db, fine_id, user.id); commit(db)
    return s.fine_record(row)


@router.get('/librarian/library/records/{kind}')
def records(kind: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
            user=Depends(staff), db=Depends(get_session)):
    if kind == 'reviews':
        return s.reviews(db, offset=offset, limit=limit)
    model = {'pickups': BookReservation, 'returns': BookIssue, 'issues': BookIssue,
             'overdue': BookIssue, 'fines': LibraryFine}.get(kind)
    if model is None:
        raise HTTPException(404, 'Unknown report')
    query = db.query(model)
    if kind == 'pickups': query = query.filter_by(status='PENDING_PICKUP')
    if kind == 'returns': query = query.filter_by(status='RETURN_PENDING')
    if kind == 'overdue': query = query.filter(BookIssue.status.in_(s.ACTIVE_ISSUES), BookIssue.due_at < utcnow())
    serialize = (lambda row: s.reservation_record(db, row)) if model == BookReservation else (
        (lambda row: s.issue_record(db, row)) if model == BookIssue else s.fine_record)
    return {'total': query.count(), 'items': [serialize(row) for row in query.order_by(model.id.desc()).offset(offset).limit(limit)]}


@router.get('/librarian/library/summary')
def staff_summary(user=Depends(staff), db=Depends(get_session)):
    active = db.query(BookIssue).filter(BookIssue.status.in_(s.ACTIVE_ISSUES))
    return {'issued_count': active.count(),
        'pending_pickups': db.query(BookReservation).filter_by(status='PENDING_PICKUP').count(),
        'pending_returns': db.query(BookIssue).filter_by(status='RETURN_PENDING').count(),
        'overdue_count': active.filter(BookIssue.due_at < utcnow()).count(),
        'unpaid_total': str(db.query(func.coalesce(func.sum(LibraryFine.amount), 0)).filter(LibraryFine.status == 'UNPAID').scalar())}


@router.get('/parent/children/{usn}/library')
def parent_library(usn: str, parent=Depends(require_parent), db=Depends(get_session)):
    child = child_for_parent(db, parent, usn)
    return s.summary(db, child.usn, parent=True)


def librarian_record(account, profile):
    return {'id': account.id, 'username': account.username, 'display_name': account.display_name,
            'active': profile.active, 'must_change_password': account.must_change_password}


@router.get('/admin/librarians')
def librarians(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
               user=Depends(admin), db=Depends(get_session)):
    query = db.query(User, LibrarianAccount).join(LibrarianAccount, LibrarianAccount.user_id == User.id).filter(User.role == 'librarian')
    return {'total': query.count(), 'items': [librarian_record(account, profile) for account, profile in
        query.order_by(User.username).offset(offset).limit(limit)]}


@router.post('/admin/librarians', status_code=201)
def create_librarian(body: LibrarianCreate, response: Response, user=Depends(admin), db=Depends(get_session)):
    if db.query(User.id).filter(func.lower(User.username) == body.username).first():
        raise HTTPException(409, 'Username is already in use')
    password = generated_password()
    account = User(username=body.username, display_name=body.display_name, role='librarian',
                   password_hash=hash_password(password), must_change_password=True)
    try:
        db.add(account); db.flush()
        profile = LibrarianAccount(user_id=account.id); db.add(profile); commit(db)
    except IntegrityError:
        db.rollback(); raise HTTPException(409, 'Username is already in use') from None
    response.headers['Cache-Control'] = 'no-store'
    return librarian_record(account, profile) | {'generated_credentials': {'username': account.username, 'temporary_password': password}}


@router.put('/admin/librarians/{user_id}')
def update_librarian(user_id: int, body: LibrarianUpdate, user=Depends(admin), db=Depends(get_session)):
    account = s.locked(db, User, user_id)
    if account.role != 'librarian':
        raise HTTPException(404, 'Librarian not found')
    profile = s.locked(db, LibrarianAccount, user_id)
    account.display_name = body.display_name; profile.active = body.active; commit(db)
    return librarian_record(account, profile)
