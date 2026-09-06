"""HOD-only aggregate assistant capability tests (isolated SQLite fixture)."""
import asyncio

from backend.app import llm
from backend.app.agents import tools
from test_read_only_chat import _ensure_chat_scope


def _hod(db):
    _ensure_chat_scope(db)
    from backend.app.models import User
    return db.query(User).filter_by(username="chat.hod").one()


def _run(agents, db, question):
    return asyncio.run(agents["orchestrator_agent"].handle_chat(db, _hod(db), question))


def test_hod_department_counts_are_aggregate_and_deterministic(agents, db, monkeypatch):
    called = []
    original = tools.execute_chat
    def execute(*args):
        called.append(args[3]); return original(*args)
    monkeypatch.setattr(tools, "execute_chat", execute)
    result = _run(agents, db, "Give me the student and faculty count for my department.")
    assert result["category"] == "department_record"
    assert result["tools_used"][0]["name"] == "get_department_summary"
    assert called == ["get_department_summary"]
    expected = agents["academic_agent"].department_summary(db, "AIML")
    assert f"{expected['student_count']} students" in result["text"]
    assert f"{expected['faculty_count']} faculty" in result["text"]


def test_hod_summary_paraphrases_never_reach_general_ai(agents, db, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("department count reached Ollama")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    for question in ("How many students are in the AIML department?",
                     "How many faculty are there in my department?",
                     "Tell me the number of students and teachers in my department."):
        assert _run(agents, db, question)["category"] == "department_record"


def test_foreign_or_unauthorized_department_summary_never_leaks(agents, db):
    from backend.app.models import User
    foreign = _run(agents, db, "How many students are in the CSE department?")
    assert foreign["category"] == "sensitive_or_disallowed"
    assert "CSE" not in foreign["text"]
    student = db.query(User).filter_by(username="4MT23AI001").one()
    result = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, student, "How many students are in my department?"))
    assert result["category"] == "unsupported"
    faculty = db.query(User).filter_by(username="chat.faculty").one()
    result = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, faculty, "How many faculty are in my department?"))
    assert result["category"] == "unsupported"


def test_department_summary_capability_is_hod_only():
    for role in ("student", "faculty", "principal", "admin"):
        assert "Department summary" not in str(tools.assistant_capabilities(role))
    assert "Department summary" in str(tools.assistant_capabilities("hod"))
