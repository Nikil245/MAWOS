import copy

import pytest
from sqlalchemy import func, select

from backend.app.models import Book, BookDepartment, Department
from scripts.import_library_catalogue import (
    DEFAULT_DATASET,
    CatalogueValidationError,
    import_catalogue,
    isbn13_is_valid,
    load_dataset,
    validate_dataset,
)


@pytest.fixture(scope="module")
def catalogue():
    return load_dataset(DEFAULT_DATASET)


def test_dataset_isbns_are_valid_and_unique(catalogue):
    isbns = [book["isbn"] for book in catalogue["books"]]
    assert all(isbn13_is_valid(isbn) for isbn in isbns)
    assert len(isbns) == len(set(isbns))


def test_dataset_has_exact_required_title_and_copy_totals(catalogue):
    report = validate_dataset(catalogue)
    assert report["total_distinct_titles"] == 150
    assert report["total_physical_copies"] == 1110
    assert report["expected_count_confirmed"] is True


def test_dataset_uses_only_current_mawos_department_codes(catalogue):
    assert {code for book in catalogue["books"] for code in book["departments"]} <= {
        "AIML", "CSE", "ECE", "ME", "CV"
    }
    invalid = copy.deepcopy(catalogue)
    invalid["books"][0]["departments"] = ["INVALID"]
    with pytest.raises(CatalogueValidationError, match="invalid department"):
        validate_dataset(invalid)


def _ensure_catalogue_departments(db):
    existing = set(db.scalars(select(Department.code)))
    for code in {"AIML", "CSE", "ECE", "ME", "CV"} - existing:
        db.add(Department(code=code, name=f"Test {code}", intake=1))
    db.flush()


def test_dry_run_writes_nothing(db, catalogue):
    _ensure_catalogue_departments(db)
    before_books = db.scalar(select(func.count()).select_from(Book))
    before_tags = db.scalar(select(func.count()).select_from(BookDepartment))

    report = import_catalogue(db, catalogue, apply=False)

    assert report["mode"] == "dry-run"
    assert report["inserted_books"] == 0
    assert report["new_book_count"] == 150
    assert len(report["new_books_that_would_be_added"]) == 150
    assert db.scalar(select(func.count()).select_from(Book)) == before_books
    assert db.scalar(select(func.count()).select_from(BookDepartment)) == before_tags
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()


def test_apply_is_guarded_and_idempotent(db, catalogue):
    _ensure_catalogue_departments(db)
    with pytest.raises(PermissionError, match="MAWOS_ALLOW_LIBRARY_CATALOGUE_IMPORT"):
        import_catalogue(db, catalogue, apply=True, allow_apply=False)

    existing_record = catalogue["books"][0]
    preexisting = Book(
        isbn=existing_record["isbn"], title="Pre-existing catalogue record",
        author="Existing author", publisher="Existing publisher", category="Existing",
        description="Must remain untouched.", total_copies=1, available_copies=1,
        popularity_count=7, is_active=False,
    )
    db.add(preexisting)
    db.commit()
    first = import_catalogue(db, catalogue, apply=True, allow_apply=True)
    db.commit()
    second = import_catalogue(db, catalogue, apply=True, allow_apply=True)
    db.refresh(preexisting)
    assert first["inserted_books"] == 149
    assert first["existing_isbns_skipped"] == [existing_record["isbn"]]
    assert second["inserted_books"] == 0
    assert second["existing_isbn_skip_count"] == 150
    assert preexisting.title == "Pre-existing catalogue record"
    assert preexisting.total_copies == preexisting.available_copies == 1
    assert preexisting.popularity_count == 7
    assert preexisting.is_active is False

    # This test owns all catalogue ISBNs it inserted; remove them to isolate the
    # shared test database from unrelated library tests.
    isbns = [book["isbn"] for book in catalogue["books"]]
    ids = list(db.scalars(select(Book.id).where(Book.isbn.in_(isbns))))
    db.query(BookDepartment).filter(BookDepartment.book_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.query(Book).filter(Book.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
