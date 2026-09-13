"""Library records use UTC timestamps; calendar fines use Asia/Kolkata."""
from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey,
                        Index, Integer, Numeric, String, Text, UniqueConstraint, func, text)
from ..database import Base
from ..models import utcnow


class Timestamps:
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now())


class Book(Timestamps, Base):
    __tablename__ = 'books'
    __table_args__ = (
        CheckConstraint('total_copies >= 0 AND available_copies >= 0 AND available_copies <= total_copies', name='ck_books_stock'),
        CheckConstraint('popularity_count >= 0', name='ck_books_popularity'),
        Index('ix_books_active_title', 'is_active', 'title'),
    )
    id = Column(Integer, primary_key=True)
    isbn = Column(String(32), nullable=False, unique=True)
    title = Column(String(256), nullable=False)
    author = Column(String(256), nullable=False)
    publisher = Column(String(256))
    category = Column(String(128), nullable=False, index=True)
    description = Column(Text)
    total_copies = Column(Integer, nullable=False)
    available_copies = Column(Integer, nullable=False)
    popularity_count = Column(Integer, nullable=False, default=0, server_default='0')
    is_active = Column(Boolean, nullable=False, default=True, server_default=text('true'))


class BookDepartment(Base):
    __tablename__ = 'book_departments'
    book_id = Column(Integer, ForeignKey('books.id'), primary_key=True)
    department_code = Column(String(8), ForeignKey('departments.code'), primary_key=True)


class BookReservation(Timestamps, Base):
    __tablename__ = 'book_reservations'
    __table_args__ = (
        CheckConstraint("status IN ('PENDING_PICKUP','COLLECTED','CANCELLED','EXPIRED')", name='ck_book_reservations_status'),
        CheckConstraint("length(slip_code) = 6 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(slip_code, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', '') = ''", name='ck_book_reservations_slip'),
        CheckConstraint('pickup_deadline > requested_at', name='ck_book_reservations_deadline'),
        Index('uq_book_reservations_pending', 'student_usn', 'book_id', unique=True,
              postgresql_where=text("status = 'PENDING_PICKUP'"), sqlite_where=text("status = 'PENDING_PICKUP'")),
        Index('ix_book_reservations_expiry', 'status', 'pickup_deadline'),
    )
    id = Column(Integer, primary_key=True)
    student_usn = Column(String(16), ForeignKey('students.usn'), nullable=False, index=True)
    book_id = Column(Integer, ForeignKey('books.id'), nullable=False, index=True)
    slip_code = Column(String(6), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default='PENDING_PICKUP')
    requested_at = Column(DateTime, nullable=False, default=utcnow)
    pickup_deadline = Column(DateTime, nullable=False)
    collected_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    expired_at = Column(DateTime)


class BookIssue(Timestamps, Base):
    __tablename__ = 'book_issues'
    __table_args__ = (
        CheckConstraint("status IN ('ISSUED','RETURN_PENDING','RETURNED')", name='ck_book_issues_status'),
        CheckConstraint('due_at > issued_at', name='ck_book_issues_due'),
        Index('ix_book_issues_active_due', 'status', 'due_at'),
        Index('ix_book_issues_student_status', 'student_usn', 'status'),
    )
    id = Column(Integer, primary_key=True)
    student_usn = Column(String(16), ForeignKey('students.usn'), nullable=False)
    book_id = Column(Integer, ForeignKey('books.id'), nullable=False, index=True)
    reservation_id = Column(Integer, ForeignKey('book_reservations.id'), unique=True)
    status = Column(String(20), nullable=False, default='ISSUED')
    issued_at = Column(DateTime, nullable=False, default=utcnow)
    due_at = Column(DateTime, nullable=False)
    return_requested_at = Column(DateTime)
    returned_at = Column(DateTime)
    return_rejection_reason = Column(String(1000))


class LibraryFine(Base):
    __tablename__ = 'library_fines'
    __table_args__ = (
        UniqueConstraint('source_type', 'source_id', name='uq_library_fines_source'),
        CheckConstraint("source_type IN ('OVERDUE_RETURN','MISSED_PICKUP')", name='ck_library_fines_source'),
        CheckConstraint("status IN ('UNPAID','PAID')", name='ck_library_fines_status'),
        CheckConstraint('amount >= 0 AND source_id > 0', name='ck_library_fines_amount'),
        Index('ix_library_fines_student_status', 'student_usn', 'status'),
    )
    id = Column(Integer, primary_key=True)
    student_usn = Column(String(16), ForeignKey('students.usn'), nullable=False)
    source_type = Column(String(24), nullable=False)
    source_id = Column(Integer, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    reason = Column(String(1000), nullable=False)
    status = Column(String(8), nullable=False, default='UNPAID')
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    paid_at = Column(DateTime)
    collected_by_user_id = Column(Integer, ForeignKey('users.id'))


class BookReview(Timestamps, Base):
    __tablename__ = 'book_reviews'
    __table_args__ = (
        CheckConstraint('rating >= 1 AND rating <= 5', name='ck_book_reviews_rating'),
        CheckConstraint('length(comment) <= 2000', name='ck_book_reviews_comment'),
    )
    id = Column(Integer, primary_key=True)
    student_usn = Column(String(16), ForeignKey('students.usn'), nullable=False)
    book_id = Column(Integer, ForeignKey('books.id'), nullable=False, index=True)
    issue_id = Column(Integer, ForeignKey('book_issues.id'), nullable=False, unique=True)
    rating = Column(Integer, nullable=False)
    comment = Column(String(2000))


class LibrarianAccount(Base):
    __tablename__ = 'librarian_accounts'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    active = Column(Boolean, nullable=False, default=True, server_default=text('true'))
