"""Phase 1 assistant contract: scoped, authorized, and database-read-only."""
import asyncio
import datetime as dt
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app import llm, router
from backend.app.agents import tools
from backend.app.auth import hash_password
from backend.app.main import app
from backend.app.models import (
    AttendanceRecord, Department, Faculty, FeeRecord, HallTicket, IntentLog,
    MarksRecord, Student, Subject, TeachingAssignment, User,
)
from backend.app.api.schemas import AssistantCapabilitiesResponse


def _ensure_chat_scope(db):
    if db.get(Department, "CSE") is None:
        db.add(Department(code="CSE", name="Computer Science", intake=2))
    if db.get(Subject, "23CS51") is None:
        db.add(Subject(code="23CS51", name="Data Structures", dept_code="CSE",
                       semester=5, credits=4))
    if db.get(Student, "4MT23AI911") is None:
        db.add(Student(usn="4MT23AI911", name="Chat Assigned", dept_code="AIML",
                       year=3, semester=5, section="A", cgpa=8.0, backlogs=0,
                       family_income=300000))
    if db.get(Student, "4MT23CS911") is None:
        db.add(Student(usn="4MT23CS911", name="Chat Foreign", dept_code="CSE",
                       year=3, semester=5, section="A", cgpa=8.0, backlogs=0,
                       family_income=300000))
    faculty = db.query(Faculty).filter_by(name="Test Prof").first()
    if db.query(User).filter_by(username="chat.faculty").first() is None:
        db.add(User(username="chat.faculty", password_hash=hash_password("x"),
                    role="faculty", display_name="Chat Faculty", faculty_id=faculty.id,
                    dept_code="AIML"))
    if db.query(User).filter_by(username="chat.hod").first() is None:
        db.add(User(username="chat.hod", password_hash=hash_password("x"),
                    role="hod", display_name="Chat HOD", dept_code="AIML"))
    if db.query(User).filter_by(username="chat.admin").first() is None:
        db.add(User(username="chat.admin", password_hash=hash_password("x"),
                    role="admin", display_name="Chat Admin"))
    if db.query(User).filter_by(username="chat.principal").first() is None:
        db.add(User(username="chat.principal", password_hash=hash_password("x"),
                    role="principal", display_name="Chat Principal"))
    db.commit()


def _headers(username):
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": username, "password": "x"})
    assert response.status_code == 200
    return client, {"Authorization": "Bearer " + response.json()["token"]}


def _domain_snapshot(db):
    return {
        "attendance": [(r.id, r.usn, r.subject_code, r.date, r.present)
                       for r in db.query(AttendanceRecord).order_by(AttendanceRecord.id)],
        "fees": [(r.id, r.usn, r.amount_paid, r.fine, r.status, r.paid_date)
                 for r in db.query(FeeRecord).order_by(FeeRecord.id)],
        "marks": [(r.id, r.usn, r.subject_code, r.internal, r.marks)
                  for r in db.query(MarksRecord).order_by(MarksRecord.id)],
        "tickets": [(r.id, r.usn, r.semester, r.eligible, r.reasons)
                    for r in db.query(HallTicket).order_by(HallTicket.id)],
        "intent_logs": db.query(IntentLog).count(),
    }


def test_chat_requires_authentication():
    response = TestClient(app).post("/api/chat", json={"message": "Show attendance"})
    assert response.status_code == 401


def test_chat_scope_and_response_contract_do_not_execute_other_services(agents, db, monkeypatch):
    client, headers = _headers("4MT23AI001")
    placement = agents["placement_agent"]
    monkeypatch.setattr(placement, "student_view", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(llm, "check_ollama", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    response = client.post("/api/chat", headers=headers, json={"message": "Show my placements"})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "scope"
    assert body["tools_used"] == []
    assert body["routing"]["tier"] == "scope"
    assert "attendance" in body["text"].lower()


def test_sensitive_input_is_rejected_before_ollama_or_persistence(agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    before_logs = db.query(IntentLog).count()
    monkeypatch.setattr(llm, "check_ollama", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "My token abc123: what fees do I owe?"))

    assert response["mode"] == "scope"
    assert response["routing"]["reason"] == "sensitive input rejected before routing"
    assert db.query(IntentLog).count() == before_logs


def test_chat_read_only_queries_never_flush_commit_or_log(agents, db, monkeypatch):
    client, headers = _headers("4MT23AI002")
    before = _domain_snapshot(db)
    commits, flushes = [], []
    original_commit, original_flush = Session.commit, Session.flush

    def track_commit(session, *args, **kwargs):
        commits.append(session)
        return original_commit(session, *args, **kwargs)

    def track_flush(session, *args, **kwargs):
        flushes.append(session)
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "commit", track_commit)
    monkeypatch.setattr(Session, "flush", track_flush)
    for message in ("What is my attendance?", "What fees do I owe?",
                    "Show my marks", "Am I eligible for a hall ticket?"):
        response = client.post("/api/chat", headers=headers, json={"message": message})
        assert response.status_code == 200
        assert response.json()["mode"] == "lexicon"
        assert response.json()["routing"]["tier"] == "lexicon"
        assert response.json()["routing"]["attempted_llm"] is False
        assert response.json()["routing"]["accepted_llm"] is False
        assert response.json()["routing"]["deterministic_fallback"] is False
        assert response.json()["fallback_code"] is None

    db.expire_all()
    assert commits == []
    assert flushes == []
    assert _domain_snapshot(db) == before


def test_hall_ticket_chat_matches_existing_rule_without_persisting(agents, db):
    user = db.query(User).filter_by(username="4MT23AI002").one()
    before = _domain_snapshot(db)

    result = tools.execute_chat(db, agents, user, "get_hall_ticket", {"usn": "4MT23AI001"})
    expected = agents["eligibility_agent"].hall_ticket_status(db, user.usn)

    assert result == expected
    assert result["eligible"] is False
    assert _domain_snapshot(db) == before


def test_chat_resource_authorization_is_not_overridden_by_tool_arguments(agents, db):
    _ensure_chat_scope(db)
    student = db.query(User).filter_by(username="4MT23AI001").one()
    faculty = db.query(User).filter_by(username="chat.faculty").one()
    hod = db.query(User).filter_by(username="chat.hod").one()

    own = tools.execute_chat(db, agents, student, "get_attendance", {"usn": "4MT23AI002"})
    assert own["usn"] == student.usn

    allowed_attendance = tools.execute_chat(
        db, agents, faculty, "get_attendance", {"usn": "4MT23AI911"})
    assert allowed_attendance["usn"] == "4MT23AI911"
    denied_attendance = tools.execute_chat(
        db, agents, faculty, "get_attendance", {"usn": "4MT23CS911"})
    assert "not authorized" in denied_attendance["error"]
    denied_hod_attendance = tools.execute_chat(
        db, agents, hod, "get_attendance", {"usn": "4MT23CS911"})
    assert "not authorized" in denied_hod_attendance["error"]

    denied_fee = tools.execute_chat(db, agents, faculty, "get_fees", {"usn": "4MT23AI911"})
    assert "no read permission" in denied_fee["error"]
    denied_hall_ticket = tools.execute_chat(db, agents, hod, "get_hall_ticket", {"usn": "4MT23AI911"})
    assert "no read permission" in denied_hall_ticket["error"]

    faculty_marks = tools.execute_chat(db, agents, faculty, "get_marks", {
        "usn": "4MT23AI911", "subject_code": "23AI51"})
    assert faculty_marks["usn"] == "4MT23AI911"
    denied_marks = tools.execute_chat(db, agents, faculty, "get_marks", {
        "usn": "4MT23AI911", "subject_code": "23AI99"})
    assert "not authorized" in denied_marks["error"]


def test_backend_capability_matrix_is_derived_from_chat_policy():
    expected = {
        "student": {"Attendance", "Fees", "Internal marks", "Hall-ticket eligibility", "Profile"},
        "faculty": {"Attendance", "Internal marks", "Profile"},
        "hod": {"Attendance", "Internal marks", "Profile", "Department summary"},
        "principal": {"Profile"},
        "admin": {"Attendance", "Internal marks", "Profile"},
    }
    for role, categories in expected.items():
        capability = tools.assistant_capabilities(role, "Example User")
        assert {item["category"] for item in capability["record_capabilities"]} == categories
        advertised = set(categories)
        for tool_name, allowed_roles in tools.CHAT_TOOL_ROLES.items():
            label = tools._CAPABILITY_DETAILS.get(role, {}).get(tool_name, (None,))[0]
            assert (label in advertised) == (role in allowed_roles and label is not None)


def test_role_capability_copy_is_scoped_and_uses_placeholders():
    student = json.dumps(tools.assistant_capabilities("student")).lower()
    faculty = json.dumps(tools.assistant_capabilities("faculty")).lower()
    hod = json.dumps(tools.assistant_capabilities("hod")).lower()
    principal = json.dumps(tools.assistant_capabilities("principal")).lower()
    admin = json.dumps(tools.assistant_capabilities("admin")).lower()

    assert all(term in student for term in ("your own attendance", "fees", "internal marks", "hall-ticket"))
    student_prompts = tools.assistant_capabilities("student")["suggestion_groups"][0]["prompts"]
    assert all("my" in prompt.lower().split() for prompt in student_prompts)
    assert "[authorized student usn]" in faculty and "[assigned subject code]" in faculty
    assert "faculty dashboard" in faculty
    assert "your own" not in faculty and "fees" not in faculty and "hall-ticket" not in faculty
    assert "your department" in hod and "institution-wide" not in hod
    assert "other personal-record chat access is not currently defined" in principal
    assert "specified student" in admin and "fees and hall-ticket eligibility are not available" in admin


def test_role_aware_help_and_general_conversation_use_no_database_tools(agents, db, monkeypatch):
    _ensure_chat_scope(db)

    async def copy(messages, tools=None, budget=None):
        reference = json.loads(messages[-1]["content"])
        return llm.OllamaResult(message={"content": json.dumps(reference)})

    monkeypatch.setattr(llm, "chat_async", copy)
    monkeypatch.setattr(tools, "execute_chat", lambda *args: (_ for _ in ()).throw(AssertionError()))
    for username in ("4MT23AI001", "chat.faculty", "chat.hod", "chat.principal", "chat.admin"):
        user = db.query(User).filter_by(username=username).one()
        for message in ("Hello", "What can you help me with?", "What is a CIE?"):
            result = asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, message))
            assert result["tools_used"] == []
        expected = tools.assistant_capabilities(user.role, user.display_name)
        greeting = asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, "Hello"))
        help_result = asyncio.run(agents["orchestrator_agent"].handle_chat(
            db, user, "What can you help me with?"))
        assert greeting["text"] == expected["greeting"]
        assert help_result["text"] == expected["help"]


def test_role_aware_missing_identifiers_and_denials_disclose_no_record_details(agents, db):
    _ensure_chat_scope(db)
    faculty = db.query(User).filter_by(username="chat.faculty").one()
    hod = db.query(User).filter_by(username="chat.hod").one()

    missing_usn = tools.execute_chat(db, agents, faculty, "get_attendance", {})["error"]
    missing_subject = tools.execute_chat(
        db, agents, faculty, "get_marks", {"usn": "4MT23AI911"})["error"]
    denied = tools.execute_chat(db, agents, faculty, "get_marks", {
        "usn": "4MT23CS911", "subject_code": "23CS51"})["error"]
    hod_missing = tools.execute_chat(db, agents, hod, "get_marks", {"usn": "4MT23AI911"})["error"]

    assert missing_usn == "Please specify an authorized student USN from one of your assigned classes."
    assert missing_subject == "Please specify one of your assigned subject codes for this marks request."
    assert denied == "You are not authorized to view that student and subject combination."
    assert "4MT23CS911" not in denied and "CSE" not in denied and "23CS51" not in denied
    assert "assigned in your department" in hod_missing


def test_capability_endpoint_is_authenticated_and_does_not_flush_or_commit(db, monkeypatch):
    _ensure_chat_scope(db)
    client, headers = _headers("chat.faculty")
    commits, flushes = [], []
    monkeypatch.setattr(Session, "commit", lambda session, *args, **kwargs: commits.append(session))
    monkeypatch.setattr(Session, "flush", lambda session, *args, **kwargs: flushes.append(session))

    response = client.get("/api/assistant/capabilities", headers=headers)

    assert response.status_code == 200
    assert response.json()["role"] == "faculty"
    assert commits == [] and flushes == []
    assert TestClient(app).get("/api/assistant/capabilities").status_code == 401


def test_capability_endpoint_uses_authenticated_backend_role_and_canonical_schema(db):
    _ensure_chat_scope(db)
    expected_roles = {
        "4MT23AI001": "student", "chat.faculty": "faculty", "chat.hod": "hod",
        "chat.principal": "principal", "chat.admin": "admin",
    }
    for username, role in expected_roles.items():
        client, headers = _headers(username)
        response = client.get("/api/assistant/capabilities?role=student", headers=headers)
        assert response.status_code == 200
        payload = AssistantCapabilitiesResponse.model_validate(response.json())
        assert payload.role == role

    invalid = TestClient(app).get(
        "/api/assistant/capabilities", headers={"Authorization": "Bearer expired-or-invalid"})
    assert invalid.status_code == 401


def test_llm_path_rejects_unsupported_forged_tool_and_falls_back(agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    calls = []

    async def fake_decide(query, budget):
        result = llm.classify_keyword(query)
        return result, router.Decision("llm", 0.0, True, "mocked LLM route")

    async def fake_chat(messages, tools=None, budget=None):
        calls.append((messages, tools))
        return llm.OllamaResult(message={"role": "assistant", "content": "", "tool_calls": [{
            "function": {"name": "get_placements", "arguments": {"usn": "4MT23AI002"}}}]})

    monkeypatch.setattr(router, "decide_async", fake_decide)
    monkeypatch.setattr(llm, "chat_async", fake_chat)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "attendance"))

    assert calls
    assert {item["function"]["name"] for item in calls[0][1]} <= tools.CHAT_READ_ONLY_TOOLS
    assert response["mode"] == "lexicon"
    assert response["routing"]["fallback_from"] == "llm"
    assert response["tools_used"][0]["name"] == "get_attendance"


def test_llm_forged_usn_is_rejected_before_tool_execution(agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    requests = []

    async def fake_decide(query, budget):
        result = llm.classify_keyword(query)
        return result, router.Decision("llm", 0.0, True, "mocked LLM route")

    async def fake_chat(messages, tools=None, budget=None):
        requests.append(messages)
        if len(requests) == 1:
            return llm.OllamaResult(message={"role": "assistant", "content": "", "tool_calls": [{
                "function": {"name": "get_attendance", "arguments": {"usn": "4MT23AI002"}}}]})
        tool_message = next(item for item in messages
                            if isinstance(item, dict) and item.get("role") == "tool")
        evidence = json.loads(tool_message["content"])
        return llm.OllamaResult(message={"role": "assistant", "content": json.dumps({
            "tool": evidence["tool"], "evidence_ref": evidence["evidence_ref"],
        })})

    monkeypatch.setattr(router, "decide_async", fake_decide)
    monkeypatch.setattr(llm, "chat_async", fake_chat)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "attendance"))

    assert len(requests) == 1
    assert response["mode"] == "lexicon"
    assert response["fallback_code"] == "tool_denied"
    assert response["routing"]["attempted_llm"] is True
    assert response["routing"]["accepted_llm"] is False
