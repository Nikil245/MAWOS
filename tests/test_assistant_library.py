"""Grounded, private, read-only catalogue support in assistant chat."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import llm
from backend.app.agents import tools
from backend.app.api.schemas import ChatResponse
from backend.app.main import app
from backend.app.models import Book, BookDepartment, Department, User


BOOKS = (
    ("9780000000101", "Python Crash Course", "Eric Matthes", "Python", 6, 4, True, ["AIML", "CSE"]),
    ("9780000000102", "Learning Python", "Mark Lutz", "Python", 6, 0, True, ["AIML", "CSE"]),
    ("9780000000103", "Machine Learning Engineering", "Andriy Burkov", "Machine Learning", 6, 3, True, ["AIML"]),
    ("9780000000104", "Hands-On Machine Learning", "Aurelien Geron", "Machine Learning", 6, 2, True, ["AIML"]),
    ("9780000000105", "Cloud Native DevOps", "John Arundel", "Cloud Computing", 8, 5, True, ["CSE"]),
    ("9780000000106", "Retired COBOL Handbook", "Archive Author", "Legacy Systems", 6, 6, False, ["CSE"]),
)


@pytest.fixture()
def library_catalogue(db):
    if db.get(Department, "CSE") is None:
        db.add(Department(code="CSE", name="Computer Science", intake=2))
    rows = []
    for isbn, title, author, category, total, available, active, departments in BOOKS:
        book = Book(isbn=isbn, title=title, author=author, publisher="Test Publisher",
                    category=category, description=f"Safe description for {title}.",
                    total_copies=total, available_copies=available,
                    popularity_count=0, is_active=active)
        db.add(book); db.flush()
        db.add_all(BookDepartment(book_id=book.id, department_code=code)
                   for code in departments)
        rows.append(book)
    db.commit()
    yield rows
    ids = [book.id for book in rows]
    db.query(BookDepartment).filter(BookDepartment.book_id.in_(ids)).delete(
        synchronize_session=False)
    db.query(Book).filter(Book.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def ask(agents, db, message, user=None):
    actor = user or db.query(User).filter_by(username="4MT23AI001").one()
    result = asyncio.run(agents["orchestrator_agent"].handle_chat(db, actor, message))
    ChatResponse.model_validate(result)
    return result


def test_student_capabilities_advertise_library_prompts_only_to_students():
    student_groups = tools.assistant_capabilities("student")["suggestion_groups"]
    library = next(group for group in student_groups if group["label"] == "Library catalogue")
    assert library["prompts"] == [
        "Find available AIML books", "Recommend a Python book from the library",
        "Check a book's availability",
    ]
    assert all(group["label"] != "Library catalogue" for group in
               tools.assistant_capabilities("faculty")["suggestion_groups"])


def test_exact_title_availability_is_deterministic_and_safe(agents, db, library_catalogue,
                                                             monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("exact catalogue lookup reached Ollama")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    result = ask(agents, db, "Is Python Crash Course available in the college library?")
    assert result["source_label"] == "Library catalogue result"
    assert result["mode"] == "lexicon"
    assert "4 of 6 copies available" in result["text"]
    assert result["data"]["books"][0] == {
        "title": "Python Crash Course", "author": "Eric Matthes",
        "isbn": "9780000000101", "publisher": "Test Publisher", "category": "Python",
        "description": "Safe description for Python Crash Course.",
        "departments": ["AIML", "CSE"], "total_copies": 6, "available_copies": 4,
        "availability_status": "available",
    }
    assert result["actions"][0]["route"] == "/student/library?q=Python%20Crash%20Course"
    assert "id" not in json.dumps(result["data"]).lower()


@pytest.mark.parametrize("question,expected", [
    ("Find books by Eric Matthes", "Python Crash Course"),
    ("Search the library for Python", "Learning Python"),
    ("Find books about Cloud Computing", "Cloud Native DevOps"),
    ("Find available AIML books", "Machine Learning Engineering"),
])
def test_keyword_category_author_and_department_search(question, expected, agents, db,
                                                        library_catalogue, monkeypatch):
    async def unavailable(*_args, **_kwargs):
        return llm.OllamaResult(error_code="unavailable")
    monkeypatch.setattr(llm, "chat_async", unavailable)
    result = ask(agents, db, question)
    assert expected in result["text"]
    assert "Retired COBOL Handbook" not in result["text"]


def test_no_match_and_ambiguous_availability(agents, db, library_catalogue, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("deterministic catalogue response reached Ollama")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    missing = ask(agents, db, "Search the library for Fortran Archaeology")
    assert "No matching active catalogue book was found" in missing["text"]
    assert missing["data"]["books"] == []
    archived = ask(agents, db, "Is Retired COBOL Handbook available in the library?")
    assert "No matching active catalogue book was found" in archived["text"]
    ambiguous = ask(agents, db, "Is Machine Learning available in the library?")
    assert "ambiguous" in ambiguous["text"].lower()
    assert "Machine Learning Engineering" in ambiguous["text"]
    assert "Hands-On Machine Learning" in ambiguous["text"]


def test_guided_recommendation_sends_only_sanitized_catalogue_and_validates_choices(
        agents, db, library_catalogue, monkeypatch):
    seen = []
    async def choose(messages, tools=None, budget=None):
        seen.extend(messages)
        assert tools is None
        return llm.OllamaResult(message={"content": '{"choices":[0]}'})
    monkeypatch.setattr(llm, "chat_async", choose)
    result = ask(agents, db, "Recommend available books for Python")
    assert result["source_label"] == "Library-guided AI response"
    assert result["mode"] == "llm"
    assert "Learning Python" not in result["text"]  # zero available copies excluded
    model_input = json.loads(seen[1]["content"])
    assert set(model_input) == {"catalogue_data"}
    assert all(set(row) == {"index", "title", "author", "category", "available_copies"}
               for row in model_input["catalogue_data"])
    assert not any(key in seen[1]["content"].lower()
                   for key in ("student_usn", "reservation", "fine", "password", "database"))


def test_ollama_unavailable_returns_deterministic_available_results(
        agents, db, library_catalogue, monkeypatch):
    async def unavailable(*_args, **_kwargs):
        return llm.OllamaResult(error_code="unavailable")
    monkeypatch.setattr(llm, "chat_async", unavailable)
    result = ask(agents, db, "Recommend a Python book from the library")
    assert result["source_label"] == "Library catalogue result"
    assert result["fallback_code"] == "unavailable"
    assert "Python Crash Course" in result["text"]
    assert "Learning Python" not in result["text"]


def test_private_and_mutating_library_requests_are_denied_without_leakage(
        agents, db, library_catalogue, monkeypatch):
    agent = agents["library_agent"]
    monkeypatch.setattr(agent, "search_catalogue", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("denied request queried the catalogue")))
    private = ask(agents, db, "Who borrowed Python Crash Course?")
    mutation = ask(agents, db, "Reserve this library book for me")
    assert private["category"] == "sensitive_or_disallowed"
    assert mutation["category"] == "unsupported"
    combined = json.dumps([private, mutation]).lower()
    assert "4mt23" not in combined and "student_usn" not in combined
    assert "read-only" in mutation["text"].lower()


def test_library_chat_is_student_only_and_api_requires_authentication(
        agents, db, library_catalogue, monkeypatch):
    faculty = SimpleNamespace(role="faculty", usn=None, display_name="Faculty")
    monkeypatch.setattr(agents["library_agent"], "search_catalogue",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("unauthorized role queried catalogue")))
    denied = ask(agents, db, "Search the library for Python", faculty)
    assert denied["category"] == "unsupported"
    assert denied["actions"] == []
    response = TestClient(app).post("/api/chat", json={"message": "Search the library for Python"})
    assert response.status_code == 401


def test_all_library_assistant_paths_are_read_only(agents, db, library_catalogue, monkeypatch):
    async def unavailable(*_args, **_kwargs):
        return llm.OllamaResult(error_code="unavailable")
    monkeypatch.setattr(llm, "chat_async", unavailable)
    before = [(book.id, book.total_copies, book.available_copies, book.is_active,
               book.popularity_count) for book in db.query(Book).order_by(Book.id)]
    before_tags = [(row.book_id, row.department_code) for row in db.query(
        BookDepartment).order_by(BookDepartment.book_id, BookDepartment.department_code)]
    for question in (
        "Is Python Crash Course available in the college library?",
        "Find books by Eric Matthes", "Recommend available books for Python",
        "Is Machine Learning available in the library?", "Search the library for missing title",
    ):
        ask(agents, db, question)
    after = [(book.id, book.total_copies, book.available_copies, book.is_active,
              book.popularity_count) for book in db.query(Book).order_by(Book.id)]
    after_tags = [(row.book_id, row.department_code) for row in db.query(
        BookDepartment).order_by(BookDepartment.book_id, BookDepartment.department_code)]
    assert after == before and after_tags == before_tags
    assert not db.new and not db.dirty and not db.deleted
