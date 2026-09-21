"""Authenticated physical library API; no public or parent mutations."""
import datetime as dt
import textwrap

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..agents import get_agents
from ..auth import get_current_user, hash_password, require_role
from ..database import get_session
from ..models import (Book, BookIssue, BookReservation, Department, LibraryFine,
                      LibrarianAccount, Student, User, utcnow)
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


@router.post('/librarian/library/books/{book_id}/unarchive')
def unarchive_book(book_id: int, user=Depends(staff), db=Depends(get_session)):
    book = s.restore_book(db, book_id); commit(db)
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


def _pdf_text(value):
    """Encode text for the built-in Helvetica font without allowing PDF syntax."""
    return str(value).encode('latin-1', 'replace').decode('latin-1').replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _pdf_lines(value, width):
    """Wrap predictable record fields, including a legacy value with no spaces."""
    return textwrap.wrap(str(value), width=width, break_long_words=True,
                         break_on_hyphens=True) or ['']


def slip_pdf(record):
    """Build a one-page, print-safe A4 collection and verification slip.

    The project deliberately has no PDF dependency.  This retains the existing
    small PDF writer while giving it a structured layout and an explicit bounds
    check so unusually long catalogue data cannot silently run off the page.
    """
    commands, page_width, margin = [], 595, 42

    def command(value):
        commands.append(value)

    def text(value, x, y, size=9, bold=False, colour='0.12 0.16 0.23'):
        command(f'BT /F{2 if bold else 1} {size} Tf {colour} rg 1 0 0 1 {x:.1f} {y:.1f} Tm ({_pdf_text(value)}) Tj ET')

    def rule(x, y, width, height, stroke='0.75 0.79 0.84', fill=None, line=0.7):
        command('q')
        if fill:
            command(f'{fill} rg {x:.1f} {y:.1f} {width:.1f} {height:.1f} re f')
        command(f'{stroke} RG {line} w {x:.1f} {y:.1f} {width:.1f} {height:.1f} re S Q')

    def wrapped(value, x, y, width, size=9, bold=False, leading=12):
        # Helvetica averages roughly half an em per character at this size.
        chars = max(12, int(width / (size * 0.52)))
        lines = _pdf_lines(value, chars)
        for line in lines:
            text(line, x, y, size, bold)
            y -= leading
        return y, len(lines)

    def section(title, y):
        rule(margin, y - 21, page_width - margin * 2, 21, fill='0.93 0.96 0.99')
        text(title.upper(), margin + 10, y - 14, 9, True, '0.08 0.25 0.48')
        return y - 31

    def field(label, value, x, y, width):
        text(label.upper(), x, y, 7, True, '0.33 0.39 0.48')
        value_y, lines = wrapped(value, x, y - 13, width, 9, False, 12)
        return value_y - 5, lines

    # Header and identity: the existing MAWOS library name is the only institution
    # branding available in the application, so no new institutional data is made up.
    rule(margin, 782, page_width - margin * 2, 22, stroke='0.08 0.25 0.48', fill='0.08 0.25 0.48', line=1)
    rule(margin, 56, page_width - margin * 2, 748, stroke='0.08 0.25 0.48', line=1)
    text('MAWOS', margin + 12, 789, 15, True, '1 1 1')
    text('UNIVERSITY ERP  /  LIBRARY SERVICES', margin + 78, 790, 8, True, '1 1 1')
    text('MAWOS Library', margin + 1, 762, 13, True, '0.08 0.25 0.48')
    text('BOOK VERIFICATION SLIP', margin + 1, 730, 18, True, '0.08 0.25 0.48')
    text('Library Collection / Verification Record', margin + 1, 714, 9, False, '0.33 0.39 0.48')

    # Upper-right metadata makes the code and transaction reference easy to find at the desk.
    meta_x, meta_y, meta_w = 377, 750, 176
    rule(meta_x, 676, meta_w, 74, stroke='0.48 0.58 0.68', fill='0.97 0.98 0.99')
    text('SLIP METADATA', meta_x + 10, meta_y - 12, 8, True, '0.08 0.25 0.48')
    text(f"Reservation ID  #{record['reservation_id']}", meta_x + 10, meta_y - 30, 9, True)
    text(f"Pickup code  {record['slip_code']}", meta_x + 10, meta_y - 46, 9, True)
    text(f"Reserved  {record['requested_at']}", meta_x + 10, meta_y - 62, 8)

    y = 670
    y = section('Student Information', y)
    student_bottom, _ = field('Student Name', record['student_name'], margin + 10, y, 235)
    usn_bottom, _ = field('USN', record['student_usn'], 310, y, 110)
    programme_bottom, _ = field('Department / Programme', record['programme'], 430, y, 120)
    student_bottom = min(student_bottom, usn_bottom, programme_bottom)
    text(record['semester'], 430, student_bottom + 13, 8, False, '0.33 0.39 0.48')
    y = student_bottom - 9

    y = section('Book Information', y)
    title_bottom, _ = field('Book Title', record['title'], margin + 10, y, 500)
    author_bottom, _ = field('Author', record['author'], margin + 10, title_bottom, 500)
    isbn_bottom, _ = field('ISBN', record['isbn'], margin + 10, author_bottom, 245)
    book_bottom, _ = field('Book ID', record['book_id'], 310, author_bottom, 240)
    y = min(isbn_bottom, book_bottom) - 9

    y = section('Collection / Reservation Information', y)
    requested_bottom, _ = field('Reservation Date', record['requested_at'], margin + 10, y, 245)
    deadline_bottom, _ = field('Pickup Deadline', record['pickup_deadline'], 310, y, 240)
    y = min(requested_bottom, deadline_bottom) - 7
    rule(margin + 10, y - 35, 500, 31, stroke='0.08 0.25 0.48', fill='0.94 0.97 1')
    text('VERIFICATION STATUS', margin + 20, y - 16, 8, True, '0.08 0.25 0.48')
    # Keep the stored enum value intact; it is what staff verify at handover.
    text(record['status'], 220, y - 17, 11, True, '0.08 0.25 0.48')
    text('Status must be checked by library staff at physical handover.', 220, y - 29, 7, False, '0.33 0.39 0.48')
    y -= 47

    y = section('Librarian Verification', y)
    rule(margin + 10, y - 58, 500, 52, fill='0.99 0.99 0.99')
    text('Verify the pickup code, student ID, current status and deadline before handover.', margin + 20, y - 18, 8)
    text('Librarian signature', margin + 20, y - 46, 7, True, '0.33 0.39 0.48')
    command(f'0.45 0.49 0.55 RG 0.6 w {margin + 20} {y - 52} m 245 {y - 52} l S')
    text('Date', 282, y - 46, 7, True, '0.33 0.39 0.48')
    command(f'0.45 0.49 0.55 RG 0.6 w 282 {y - 52} m 390 {y - 52} l S')
    text('Library stamp', 426, y - 46, 7, True, '0.33 0.39 0.48')
    command(f'0.45 0.49 0.55 RG 0.6 w 426 {y - 52} m 532 {y - 52} l S')
    y -= 73

    # This assertion is a regression guard for maximum-length catalogue fields.
    if y < 76:
        raise ValueError('Library slip content exceeds the printable A4 area')
    command(f'0.75 0.79 0.84 RG 0.5 w {margin} 76 m {page_width - margin} 76 l S')
    text('Bring this slip and your student ID to the library. Validity is checked at physical handover.', margin, 64, 8, False, '0.33 0.39 0.48')
    text('MAWOS Library  |  Page 1 of 1', 401, 64, 8, False, '0.33 0.39 0.48')

    content = '\n'.join(commands).encode('latin-1')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>',
        f'<< /Length {len(content)} >>\nstream\n'.encode() + content + b'\nendstream']
    data = b'%PDF-1.4\n'; offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 7\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return data + f'trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()


@router.get('/student/library/reservations/{reservation_id}/slip.pdf')
def pdf(reservation_id: int, user=Depends(student), db=Depends(get_session)):
    row = s.owned(db, BookReservation, reservation_id, student_usn(user))
    book, profile = db.get(Book, row.book_id), db.get(Student, row.student_usn)
    department = db.get(Department, profile.dept_code)
    date_format = '%d %b %Y %H:%M IST'
    ist = lambda value: value.replace(tzinfo=dt.timezone.utc).astimezone(s.INDIA).strftime(date_format)
    data = slip_pdf({
        'reservation_id': row.id, 'slip_code': row.slip_code, 'requested_at': ist(row.requested_at),
        'student_name': profile.name, 'student_usn': row.student_usn,
        'programme': department.name if department else profile.dept_code,
        'semester': f'Semester {profile.semester} / Year {profile.year}',
        'title': book.title, 'author': book.author, 'isbn': book.isbn, 'book_id': book.id,
        'pickup_deadline': ist(row.pickup_deadline), 'status': row.status,
    })
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
