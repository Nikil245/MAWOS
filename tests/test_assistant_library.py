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
from test_read_only_chat import _headers


BOOKS = (
    ("9780000000101", "Python Crash Course", "Eric Matthes", "Python", 6, 4, True, ["AIML", "CSE"]),
    ("9780000000102", "Learning Python", "Mark Lutz", "Python", 6, 0, True, ["AIML", "CSE"]),
    ("9780000000103", "Machine Learning Engineering", "Andriy Burkov", "Machine Learning", 6, 3, True, ["AIML"]),
    ("9780000000104", "Hands-On Machine Learning", "Aurelien Geron", "Machine Learning", 6, 2, True, ["AIML"]),
    ("9780000000105", "Cloud Native DevOps", "John Arundel", "Cloud Computing", 8, 5, True, ["CSE"]),
    ("9780000000106", "Retired COBOL Handbook", "Archive Author", "Legacy Systems", 6, 6, False, ["CSE"]),
    ("9780134685991", "Effective Java", "Joshua Bloch", "Java", 5, 3, True, ["CSE"]),
    ("9781718504103", "Eloquent JavaScript", "Marijn Haverbeke", "JavaScript", 4, 2, True, ["CSE"]),
    ("9781491910771", "Head First Java", "Kathy Sierra", "Java", 6, 4, True, ["CSE"]),
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


def ask(agents, db, message, user=None, context=None):
    actor = user or db.query(User).filter_by(username="4MT23AI001").one()
    result = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, actor, message, conversation_context=context or []))
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
    assert result["source_label"] == "Generated by local AI"
    assert result["mode"] == "llm"
    assert "Learning Python" not in result["text"]  # zero available copies excluded
    model_input = json.loads(seen[1]["content"])
    assert set(model_input) == {"catalogue_data"}
    assert all(set(row) == {"index", "title", "author", "category"}
               for row in model_input["catalogue_data"])
    assert not any(key in seen[1]["content"].lower()
                   for key in ("student_usn", "reservation", "fine", "password", "database"))


def test_ollama_timeout_returns_deterministic_available_results(
        agents, db, library_catalogue, monkeypatch):
    async def timeout(*_args, **_kwargs):
        return llm.OllamaResult(error_code="timeout")
    monkeypatch.setattr(llm, "chat_async", timeout)
    result = ask(agents, db, "Recommend a Python book from the library")
    assert result["source_label"] == "Library catalogue result"
    assert result["fallback_code"] == "timeout"
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


def library_context(*books):
    return [
        {"role": "user", "category": "library_catalogue",
         "content": "Find the requested library book."},
        {"role": "assistant", "category": "library_catalogue",
         "content": "Catalogue result.",
         "books": [{"title": title, "isbn": isbn} for title, isbn in books]},
    ]


def test_library_follow_up_uses_exact_canonical_book_and_live_stock(
        agents, db, library_catalogue, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("canonical availability reached Ollama")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    book = next(row for row in library_catalogue if row.isbn == "9780000000101")
    context = library_context((book.title, book.isbn))
    book.available_copies = 2
    db.flush()
    result = ask(agents, db, "is it available?", context=context)
    assert "Python Crash Course" in result["text"]
    assert "2 of 6 copies available" in result["text"]
    assert result["context_books"] == [
        {"title": "Python Crash Course", "isbn": "9780000000101",
         "author": "Eric Matthes", "category": "Python"}]


def test_general_suggestion_not_in_catalogue_is_not_claimed_available(
        agents, db, library_catalogue, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("general recommendation follow-up reached Ollama")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    context = [
        {"role": "user", "category": "general_ai",
         "content": "Recommend a Python learning reference."},
        {"role": "assistant", "category": "general_ai",
         "content": "Try Fluent Python by Luciano Ramalho."},
    ]
    result = ask(agents, db, "is it available in the library?", context=context)
    assert result["text"].startswith(
        "I suggested this as a general reference, but it is not currently confirmed in "
        "the MAWOS library catalogue.")
    assert "Python Crash Course" in result["text"]
    assert "Fluent Python" not in [book["title"] for book in result["data"]["books"]]


def test_ambiguous_book_context_asks_user_to_choose(agents, db, library_catalogue, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("ambiguous catalogue follow-up reached Ollama")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    context = library_context(
        ("Python Crash Course", "9780000000101"),
        ("Learning Python", "9780000000102"),
    )
    result = ask(agents, db, "Who wrote it?", context=context)
    assert "multiple books in context" in result["text"]
    assert "Choose" in result["text"]


def test_contextual_reservation_is_read_only_and_resolves_the_book(
        agents, db, library_catalogue, monkeypatch):
    for method in ("reserve", "issue_book", "request_return", "confirm_return"):
        monkeypatch.setattr(agents["library_agent"], method, lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("library follow-up attempted a write")))
    before = [(book.id, book.available_copies) for book in db.query(Book).order_by(Book.id)]
    result = ask(agents, db, "Can I reserve it?", context=library_context(
        ("Python Crash Course", "9780000000101")))
    after = [(book.id, book.available_copies) for book in db.query(Book).order_by(Book.id)]
    assert "Python Crash Course" in result["text"]
    assert "read-only" in result["text"]
    assert before == after
    assert not db.new and not db.dirty and not db.deleted


def test_exact_ui_api_contract_preserves_three_java_candidates(
        agents, db, library_catalogue, monkeypatch):
    calls = []

    async def choose_java(messages, **_kwargs):
        calls.append(messages)
        catalogue = json.loads(messages[1]["content"])["catalogue_data"]
        by_title = {book["title"]: book["index"] for book in catalogue}
        return llm.OllamaResult(message={"content": json.dumps({"choices": [
            by_title["Effective Java"], by_title["Eloquent JavaScript"],
            by_title["Head First Java"],
        ]})})

    monkeypatch.setattr(llm, "chat_async", choose_java)
    client, headers = _headers("4MT23AI001")
    first = client.post("/api/chat", headers=headers, json={"message": "recommend a Java book"})
    assert first.status_code == 200
    recommendation = first.json()
    assert recommendation["source_label"] == "Generated by local AI"
    assert [book["title"] for book in recommendation["context_books"]] == [
        "Effective Java", "Eloquent JavaScript", "Head First Java"]
    assert all(set(book) == {"title", "isbn", "author", "category"}
               for book in recommendation["context_books"])
    context = [
        {"role": "user", "category": "library_catalogue", "content": "recommend a Java book"},
        {"role": "assistant", "category": "library_catalogue",
         "content": recommendation["text"][:700], "books": recommendation["context_books"],
         "issued_at": recommendation["context_issued_at"],
         "proof": recommendation["context_proof"]},
    ]
    second = client.post("/api/chat", headers=headers, json={
        "message": "which book is best among the three books",
        "conversation_context": context,
    })
    assert second.status_code == 200
    comparison = second.json()
    assert "Best choice: Effective Java" in comparison["text"]
    assert "JavaScript—not Java" in comparison["text"]
    assert "specify the three books" not in comparison["text"].lower()
    assert len(calls) == 1  # only the initial catalogue-guided selection used Ollama


@pytest.mark.parametrize("question,expected", [
    ("is the second one available", "Learning Python: 1 of 6 copies available"),
    ("how many copies does the first book have", "Python Crash Course: 4 of 6 copies available"),
    ("who is the author of the third book", "Machine Learning Engineering is Andriy Burkov"),
])
def test_ordinal_candidate_followups_use_live_catalogue(
        question, expected, agents, db, library_catalogue, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("ordinal follow-up reached Ollama")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    context = library_context(
        ("Python Crash Course", "9780000000101"),
        ("Learning Python", "9780000000102"),
        ("Machine Learning Engineering", "9780000000103"),
    )
    if question == "is the second one available":
        next(book for book in library_catalogue if book.isbn == "9780000000102").available_copies = 1
        db.flush()
    result = ask(agents, db, question, context=context)
    assert expected in result["text"]


def test_beginner_comparison_is_grounded_and_candidate_expiry_is_explicit(
        agents, db, library_catalogue, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("deterministic comparison reached Ollama")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    context = library_context(
        ("Effective Java", "9780134685991"),
        ("Eloquent JavaScript", "9781718504103"),
        ("Head First Java", "9781491910771"),
    )
    beginner = ask(agents, db, "Which is best for a beginner?", context=context)
    assert "Best choice: Head First Java" in beginner["text"]
    assert "JavaScript—not Java" in beginner["text"]
    context[1]["candidate_set_expired"] = True
    expired = ask(agents, db, "Compare these books", context=context)
    assert "candidate set has expired" in expired["text"]
    assert "Effective Java" in expired["text"]
