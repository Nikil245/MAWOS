#!/usr/bin/env python3
"""Validate and safely import the versioned MAWOS library catalogue.

The command is a dry-run unless --apply is explicitly supplied. Applying also
requires MAWOS_ALLOW_LIBRARY_CATALOGUE_IMPORT=true.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_DATASET = ROOT / "data" / "library_catalogue_v1.json"
VALID_DEPARTMENTS = frozenset({"AIML", "CSE", "ECE", "ME", "CV"})
EXPECTED_GROUPS = {
    "shared_foundation": (20, 200),
    "programming_software_fullstack": (25, 250),
    "emerging_technology": (15, 120),
    "department_core_AIML": (18, 108),
    "department_core_CSE": (18, 108),
    "department_core_ECE": (18, 108),
    "department_core_ME": (18, 108),
    "department_core_CV": (18, 108),
}
EXPECTED_COPIES_PER_TITLE = {
    "shared_foundation": 10,
    "programming_software_fullstack": 10,
    "emerging_technology": 8,
    "department_core_AIML": 6,
    "department_core_CSE": 6,
    "department_core_ECE": 6,
    "department_core_ME": 6,
    "department_core_CV": 6,
}


class CatalogueValidationError(ValueError):
    """The frozen catalogue is unsafe to import."""


def isbn13_is_valid(value: str) -> bool:
    """Validate a normalized ISBN-13, including its check digit."""
    return (
        len(value) == 13
        and value.isdigit()
        and sum((1 if index % 2 == 0 else 3) * int(digit)
                for index, digit in enumerate(value)) % 10 == 0
    )


def load_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or not isinstance(payload.get("books"), list):
        raise CatalogueValidationError("Dataset must be an object containing a books list")
    return payload


def validate_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    books = payload.get("books")
    if not isinstance(books, list):
        raise CatalogueValidationError("Dataset must contain a books list")

    required = {"isbn", "title", "author", "publisher", "category", "departments",
                "description", "total_copies", "is_active", "edition",
                "published_date", "metadata_source", "group"}
    invalid_isbns: list[str] = []
    invalid_departments: list[str] = []
    malformed: list[str] = []
    isbns: list[str] = []
    normalized_titles: list[str] = []
    group_totals: Counter[str] = Counter()
    group_copies: Counter[str] = Counter()

    for index, book in enumerate(books, 1):
        label = f"book {index}"
        if not isinstance(book, dict):
            malformed.append(f"{label}: not an object")
            continue
        missing = required - set(book)
        if missing:
            malformed.append(f"{label}: missing {', '.join(sorted(missing))}")
            continue
        isbn = str(book["isbn"])
        isbns.append(isbn)
        if not isbn13_is_valid(isbn):
            invalid_isbns.append(isbn)
        title = str(book["title"]).strip()
        normalized_titles.append(" ".join(title.casefold().split()))
        if not title or not str(book["author"]).strip() or not str(book["publisher"]).strip():
            malformed.append(f"{label}: title, author, and publisher are required")
        departments = book["departments"]
        if not isinstance(departments, list):
            malformed.append(f"{label}: departments must be a list")
            departments = []
        invalid_departments.extend(str(code) for code in departments
                                   if code not in VALID_DEPARTMENTS)
        copies = book["total_copies"]
        if isinstance(copies, bool) or not isinstance(copies, int) or copies < 6:
            malformed.append(f"{label}: total_copies must be an integer of at least 6")
            copies = 0
        if book["is_active"] is not True:
            malformed.append(f"{label}: is_active must be true")
        group = str(book["group"])
        group_totals[group] += 1
        group_copies[group] += copies
        expected_copies = EXPECTED_COPIES_PER_TITLE.get(group)
        if expected_copies is not None and copies != expected_copies:
            malformed.append(
                f"{label}: {group} requires exactly {expected_copies} copies"
            )
        if group == "shared_foundation" and departments:
            malformed.append(f"{label}: shared foundation books must have no departments")
        if group == "programming_software_fullstack" and (
            set(departments) - {"AIML", "CSE"}
        ):
            malformed.append(f"{label}: programming relevance may use only AIML and CSE")
        if group.startswith("department_core_"):
            expected_department = group.removeprefix("department_core_")
            if departments != [expected_department]:
                malformed.append(f"{label}: {group} must tag only {expected_department}")
            expected_phrase = f"Recommended for {expected_department} Year "
            if expected_phrase not in str(book["description"]):
                malformed.append(f"{label}: department-core description lacks year relevance")

    duplicate_isbns = sorted(isbn for isbn, count in Counter(isbns).items() if count > 1)
    duplicate_titles = sorted(title for title, count in Counter(normalized_titles).items()
                              if count > 1)
    unexpected_groups = sorted(set(group_totals) - set(EXPECTED_GROUPS))
    bad_groups = {
        group: {"actual": [group_totals[group], group_copies[group]], "expected": list(expected)}
        for group, expected in EXPECTED_GROUPS.items()
        if (group_totals[group], group_copies[group]) != expected
    }
    errors = []
    if len(books) != 150:
        errors.append(f"expected 150 titles, found {len(books)}")
    total_copies = sum(book.get("total_copies", 0) for book in books
                       if isinstance(book, dict) and isinstance(book.get("total_copies"), int)
                       and not isinstance(book.get("total_copies"), bool))
    if total_copies != 1110:
        errors.append(f"expected 1110 copies, found {total_copies}")
    if invalid_isbns:
        errors.append("invalid ISBN-13 values found")
    if duplicate_isbns:
        errors.append("duplicate ISBNs found")
    if duplicate_titles:
        errors.append("duplicate titles found")
    if invalid_departments:
        errors.append("invalid department codes found")
    if malformed:
        errors.append("malformed book records found")
    if unexpected_groups or bad_groups:
        errors.append("catalogue group counts do not match the specification")

    report = {
        "total_distinct_titles": len(set(normalized_titles)),
        "total_physical_copies": total_copies,
        "invalid_isbns": sorted(set(invalid_isbns)),
        "duplicate_isbns": duplicate_isbns,
        "duplicate_titles": duplicate_titles,
        "invalid_department_codes": sorted(set(invalid_departments)),
        "malformed_records": malformed,
        "group_mismatches": bad_groups,
        "unexpected_groups": unexpected_groups,
        "expected_count_confirmed": len(books) == 150 and total_copies == 1110,
        "errors": errors,
    }
    if errors:
        raise CatalogueValidationError(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def _breakdown(books: list[dict[str, Any]], field: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for book in books:
        values = book[field] if field == "departments" else [book[field]]
        if field == "departments" and not values:
            values = ["ALL"]
        for value in values:
            row = result.setdefault(value, {"titles": 0, "copies": 0})
            row["titles"] += 1
            row["copies"] += book["total_copies"]
    return dict(sorted(result.items()))


def import_catalogue(db, payload: dict[str, Any], *, apply: bool = False,
                     allow_apply: bool | None = None) -> dict[str, Any]:
    """Validate, report, and optionally stage missing catalogue rows in ``db``.

    The caller owns commit/rollback. No ORM objects are added in dry-run mode.
    """
    validation = validate_dataset(payload)
    if apply:
        allowed = (os.getenv("MAWOS_ALLOW_LIBRARY_CATALOGUE_IMPORT", "").strip().lower() == "true"
                   if allow_apply is None else allow_apply)
        if not allowed:
            raise PermissionError(
                "--apply requires MAWOS_ALLOW_LIBRARY_CATALOGUE_IMPORT=true"
            )

    from backend.app.models import Book, BookDepartment, Department

    books = payload["books"]
    dataset_isbns = [book["isbn"] for book in books]
    existing_isbns = set(db.scalars(select(Book.isbn).where(Book.isbn.in_(dataset_isbns))))
    database_departments = set(db.scalars(select(Department.code).where(
        Department.code.in_(VALID_DEPARTMENTS))))
    required_departments = {code for book in books for code in book["departments"]}
    missing_database_departments = sorted(required_departments - database_departments)
    if missing_database_departments:
        raise CatalogueValidationError(
            "Configured database is missing departments required by the catalogue: "
            + ", ".join(missing_database_departments)
        )

    new_books = [book for book in books if book["isbn"] not in existing_isbns]
    inserted = 0
    if apply:
        for record in new_books:
            book = Book(
                isbn=record["isbn"], title=record["title"], author=record["author"],
                publisher=record["publisher"], category=record["category"],
                description=record["description"], total_copies=record["total_copies"],
                available_copies=record["total_copies"], popularity_count=0,
                is_active=True,
            )
            db.add(book)
            db.flush()
            db.add_all(BookDepartment(book_id=book.id, department_code=code)
                       for code in record["departments"])
            inserted += 1
        db.flush()

    return {
        "mode": "apply" if apply else "dry-run",
        **validation,
        "titles_and_copies_by_department": _breakdown(books, "departments"),
        "titles_and_copies_by_category": _breakdown(books, "category"),
        "existing_isbns_skipped": sorted(existing_isbns),
        "existing_isbn_skip_count": len(existing_isbns),
        "new_books_that_would_be_added": [
            {"isbn": book["isbn"], "title": book["title"], "copies": book["total_copies"]}
            for book in new_books
        ],
        "new_book_count": len(new_books),
        "new_physical_copies_that_would_be_added": sum(book["total_copies"] for book in new_books),
        "inserted_books": inserted,
        "inserted_physical_copies": (sum(book["total_copies"] for book in new_books)
                                      if apply else 0),
    }


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate and preview (default)")
    mode.add_argument("--apply", action="store_true", help="insert only ISBNs not already present")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    return parser


def main() -> int:
    parser = argument_parser()
    args = parser.parse_args()
    if args.apply and os.getenv(
        "MAWOS_ALLOW_LIBRARY_CATALOGUE_IMPORT", ""
    ).strip().lower() != "true":
        parser.error("--apply requires MAWOS_ALLOW_LIBRARY_CATALOGUE_IMPORT=true")
    payload = load_dataset(args.dataset)
    validate_dataset(payload)
    from backend.app.database import SessionLocal

    with SessionLocal() as db:
        try:
            if not args.apply and db.bind.dialect.name == "postgresql":
                db.execute(text("SET TRANSACTION READ ONLY"))
            report = import_catalogue(db, payload, apply=args.apply,
                                      allow_apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            raise
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
