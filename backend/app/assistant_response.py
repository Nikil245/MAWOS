"""Build the strict, presentation-safe assistant response contract.

This module runs only after an existing MAWOS authorization/read boundary has
produced a result.  It never queries the database and never grants access; it
only projects already-authorized DTOs into closed Pydantic UI blocks.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, time
from typing import Any

from pydantic import ValidationError

from .api.schemas import ChatResponse, LinkActionBlock


_TAG = re.compile(r"<[^>]{0,500}>")
_SCRIPT = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _plain(value: Any, limit: int = 1000) -> str:
    """Normalize untrusted/provider text for plain React text nodes."""
    text = html.unescape("" if value is None else str(value))
    text = _SCRIPT.sub("", text)
    text = _TAG.sub("", text)
    text = _CONTROL.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return _plain(value, 240)


def _source(result: dict) -> str:
    if result.get("fallback") or result.get("source_label") in {"Safe fallback", "Clarification"}:
        return "safe_fallback"
    if result.get("provider") == "groq" and result.get("mode") in {"llm", "general_ai"}:
        return "groq"
    if result.get("provider") == "ollama" and result.get("mode") in {"llm", "general_ai"}:
        return "ollama"
    return "deterministic"


def _subject_parts(row: dict) -> tuple[str, str]:
    code = _plain(row.get("subject_code"), 40)
    label = _plain(row.get("subject"), 160)
    if not code:
        match = re.search(r"\(([^()]{1,40})\)\s*$", label)
        code = match.group(1) if match else label[:40] or "Subject"
        if match:
            label = label[:match.start()].strip()
    return code or "Subject", label or code or "Subject"


def _attendance(data: dict) -> tuple[str, list[dict]]:
    subjects = data.get("subjects") if isinstance(data.get("subjects"), list) else []
    rows, typed = [], []
    attended = total = below = 0
    for item in subjects[:50]:
        if not isinstance(item, dict):
            continue
        code, name = _subject_parts(item)
        present = max(0, int(item.get("attended") or 0))
        held = max(0, int(item.get("held") or 0))
        pct = max(0.0, min(100.0, float(item.get("pct") or 0)))
        shortage = bool(item.get("shortage")) or pct < 75
        status = "Below threshold" if shortage else "On track"
        attended += present
        total += held
        below += int(shortage)
        typed.append({"subject_code": code, "subject_name": name, "attended": present,
                      "total": held, "percentage": round(pct, 2), "status": status})
        rows.append({"subject_code": code, "subject_name": name, "attended": present,
                     "total": held, "percentage": f"{pct:.1f}%", "status": status})
    overall = max(0.0, min(100.0, float(data.get("overall_pct") or 0)))
    risk = "below the 75% threshold" if overall < 75 or below else "on track"
    summary = f"Overall attendance is {overall:.1f}% and is {risk}."
    blocks: list[dict] = [{"type": "metric_cards", "title": "Attendance overview", "cards": [
        {"label": "Overall", "value": f"{overall:.1f}%",
         "detail": "Required threshold: 75%", "tone": "warning" if overall < 75 else "success"},
        {"label": "Attended classes", "value": str(attended), "tone": "info"},
        {"label": "Total classes", "value": str(total), "tone": "neutral"},
        {"label": "Below threshold", "value": str(below),
         "detail": "Subjects requiring attention", "tone": "danger" if below else "success"},
    ]}, {"type": "attendance_summary", "overall_percentage": round(overall, 2),
          "threshold_percentage": 75, "subjects": typed}]
    if rows:
        blocks.append({"type": "table", "title": "Subject attendance",
                       "caption": "Authorized subject-wise attendance record",
                       "columns": [
                           {"key": "subject_code", "label": "Subject code"},
                           {"key": "subject_name", "label": "Subject name"},
                           {"key": "attended", "label": "Attended"},
                           {"key": "total", "label": "Total"},
                           {"key": "percentage", "label": "Percentage"},
                           {"key": "status", "label": "Status"},
                       ], "rows": rows})
    else:
        blocks.append({"type": "empty_state", "title": "No attendance rows",
                       "message": "No subject attendance records are currently available in your authorized scope."})
    return summary, blocks


def _marks(data: dict) -> tuple[str, list[dict]]:
    marks = data.get("marks") if isinstance(data.get("marks"), list) else []
    subjects = []
    for row in marks[:50]:
        if not isinstance(row, dict):
            continue
        internals = {str(key)[:40]: float(value) for key, value in (row.get("internals") or {}).items()
                     if isinstance(value, (int, float)) and not isinstance(value, bool)}
        subjects.append({"subject_code": _plain(row.get("subject"), 40) or "Subject",
                         "subject_name": _plain(row.get("name"), 160) or _plain(row.get("subject"), 160) or "Subject",
                         "internals": internals, "cie_average": row.get("cie_average")})
    if not subjects:
        return "No internal marks are currently available in this authorized scope.", [{
            "type": "empty_state", "title": "No marks found",
            "message": "No internal-mark records matched this request."}]
    averages = [item["cie_average"] for item in subjects if isinstance(item.get("cie_average"), (int, float))]
    overall = sum(averages) / len(averages) if averages else None
    summary = f"Internal marks are available for {len(subjects)} subject{'s' if len(subjects) != 1 else ''}."
    cards = [{"label": "Subjects", "value": str(len(subjects)), "tone": "info"}]
    if overall is not None:
        cards.append({"label": "Average CIE", "value": f"{overall:.1f}", "tone": "neutral"})
    return summary, [{"type": "metric_cards", "title": "Marks overview", "cards": cards},
                     {"type": "marks_summary", "title": "Internal marks", "subjects": subjects}]


def _fees(data: dict) -> tuple[str, list[dict]]:
    cleared = bool(data.get("cleared"))
    outstanding = float(data.get("total_outstanding") or 0)
    summary = "All recorded fees are cleared." if cleared else f"The recorded outstanding balance is ₹{outstanding:,.0f}."
    blocks: list[dict] = [{"type": "metric_cards", "title": "Fee status", "cards": [
        {"label": "Outstanding", "value": f"₹{outstanding:,.0f}",
         "tone": "success" if cleared else "warning"},
        {"label": "Status", "value": "Cleared" if cleared else "Payment due",
         "tone": "success" if cleared else "warning"},
    ]}]
    rows = []
    for item in data.get("items", [])[:50] if isinstance(data.get("items"), list) else []:
        rows.append({"fee_type": _display(item.get("type")), "amount_due": f"₹{float(item.get('amount_due') or 0):,.0f}",
                     "fine": f"₹{float(item.get('fine') or 0):,.0f}", "due_date": _display(item.get("due_date")),
                     "status": _display(item.get("status"))})
    if rows:
        blocks.append({"type": "table", "title": "Fee items", "caption": "Authorized fee items",
                       "columns": [{"key": "fee_type", "label": "Fee type"},
                                   {"key": "amount_due", "label": "Amount due"},
                                   {"key": "fine", "label": "Fine"},
                                   {"key": "due_date", "label": "Due date"},
                                   {"key": "status", "label": "Status"}], "rows": rows})
    return summary, blocks


def _placements(data: dict) -> tuple[str, list[dict]]:
    if data.get("placement_summary"):
        eligible = int(data.get("placement_eligible_count") or 0)
        if not data.get("offer_data_available"):
            return "Placement offer records are not available in the current MAWOS database.", [
                {"type": "metric_cards", "title": "Placement overview", "cards": [
                    {"label": "Eligible / shortlisted", "value": str(eligible), "tone": "info"},
                    {"label": "Confirmed offers", "value": "Unavailable", "tone": "warning"},
                ]},
                {"type": "empty_state", "title": "Offer records unavailable",
                 "message": "The current database does not provide confirmed placement-offer records."},
            ]
        offers = data.get("offers") if isinstance(data.get("offers"), list) else []
        count = int(data.get("confirmed_offer_count") or 0)
        summary = ("No placement records are available in your authorized scope."
                   if not data.get("placement_records_available") else
                   f"You have {count} confirmed placement offer{'s' if count != 1 else ''}."
                   if count else "No confirmed placement offers are recorded for you yet.")
        blocks: list[dict] = [{"type": "metric_cards", "title": "Placement overview", "cards": [
            {"label": "Eligible / shortlisted", "value": str(eligible), "tone": "info"},
            {"label": "Confirmed offers", "value": str(count),
             "tone": "success" if count else "neutral"},
        ]}]
        cards = []
        for row in offers[:20]:
            if not isinstance(row, dict):
                continue
            cards.append({"company": _plain(row.get("company"), 128) or "Company",
                          "role": _plain(row.get("role"), 128) or "Role not specified",
                          "package": (f"{float(row['package_offered']):g} LPA"
                                      if row.get("package_offered") is not None else None),
                          "deadline": (_display(row.get("application_deadline"))
                                       if row.get("application_deadline") else None),
                          "eligibility": "Confirmed offer",
                          "status": _plain(row.get("status"), 80) or None,
                          "apply_url": row.get("application_url")})
        if cards:
            blocks.append({"type": "placement_cards", "title": "Recorded offers", "cards": cards})
        else:
            blocks.append({"type": "empty_state", "title": "No confirmed offers",
                           "message": "No confirmed placement offers are recorded in your authorized scope."})
        return summary, blocks
    drives = data.get("drives") if isinstance(data.get("drives"), list) else []
    cards = []
    for row in drives[:15]:
        if not isinstance(row, dict):
            continue
        eligible = row.get("eligible") is True
        can_apply = row.get("can_apply", eligible)
        application_url = row.get("application_url") if can_apply else None
        cards.append({"company": _plain(row.get("company"), 128) or "Company",
                      "role": _plain(row.get("role"), 128) or "Role not specified",
                      "package": f"{float(row['package_lpa']):g} LPA" if row.get("package_lpa") is not None else None,
                      "date": _display(row.get("date")) if row.get("date") else None,
                      "deadline": _display(row.get("application_deadline")) if row.get("application_deadline") else None,
                      "eligibility": "Eligible" if eligible else _plain(row.get("eligibility_status") or row.get("reasons") or "Not eligible", 120),
                      "status": _plain(row.get("status"), 80) or None,
                      "apply_url": application_url})
    if not cards:
        return "No placement drives are currently available in your authorized scope.", [{
            "type": "empty_state", "title": "No placement drives",
            "message": "There are no current placement opportunities to show."}]
    eligible_count = sum(card["eligibility"] == "Eligible" for card in cards)
    deadlines = sorted(card["deadline"] for card in cards if card.get("deadline"))
    nearest = f" The nearest deadline is {deadlines[0]}." if deadlines else ""
    summary = f"You are eligible for {eligible_count} of {len(cards)} current placement drive{'s' if len(cards) != 1 else ''}.{nearest}"
    return summary, [{"type": "metric_cards", "title": "Placement overview", "cards": [
        {"label": "Current drives", "value": str(len(cards)), "tone": "info"},
        {"label": "Eligible", "value": str(eligible_count), "tone": "success" if eligible_count else "neutral"},
        {"label": "Nearest deadline", "value": deadlines[0] if deadlines else "Not listed", "tone": "warning" if deadlines else "neutral"},
    ]}, {"type": "placement_cards", "title": "Placement opportunities", "cards": cards}]


def _library(data: dict) -> tuple[str, list[dict]]:
    books = data.get("books") if isinstance(data.get("books"), list) else []
    cards = []
    for book in books[:20]:
        if not isinstance(book, dict):
            continue
        available = max(0, int(book.get("available_copies") or 0))
        cards.append({"title": _plain(book.get("title"), 240) or "Untitled",
                      "author": _plain(book.get("author"), 240) or "Author not listed",
                      "category": _plain(book.get("category"), 120) or "Uncategorised",
                      "available_copies": available,
                      "total_copies": max(0, int(book.get("total_copies") or 0)) if book.get("total_copies") is not None else None,
                      "availability": _plain(book.get("availability_status"), 80) or ("Available" if available else "Unavailable"),
                      "isbn": _plain(book.get("isbn"), 32) or None})
    if not cards:
        return "No matching active catalogue books were found.", [{
            "type": "empty_state", "title": "No catalogue matches",
            "message": "Try a different title, author, ISBN, category, or subject keyword."}]
    available = sum(card["available_copies"] > 0 for card in cards)
    return f"Found {len(cards)} matching catalogue book{'s' if len(cards) != 1 else ''}; {available} currently have copies available.", [{
        "type": "library_book_cards", "title": "Library catalogue results", "books": cards}]


def _events(data: dict) -> tuple[str, list[dict]]:
    events = []
    for row in data.get("events", [])[:30] if isinstance(data.get("events"), list) else []:
        start, end = row.get("start_time"), row.get("end_time")
        event_time = " – ".join(value for value in (_display(start) if start else "", _display(end) if end else "") if value)
        events.append({"title": _plain(row.get("title"), 240) or "Campus event",
                       "date": _display(row.get("event_date")), "time": event_time or None,
                       "venue": _plain(row.get("venue"), 160) or None,
                       "organizer": _plain(row.get("organizer"), 160) or None,
                       "description": _plain(row.get("description"), 1000) or None})
    if not events:
        return "No visible upcoming campus events were found.", [{"type": "empty_state",
            "title": "No upcoming events", "message": "There are no published events for your authorized audience."}]
    return f"There are {len(events)} visible upcoming campus event{'s' if len(events) != 1 else ''}.", [{
        "type": "event_cards", "title": "Upcoming campus events", "events": events}]


def _timetable(data: dict) -> tuple[str, list[dict]]:
    source = data.get("weekly") or data.get("today") or []
    entries = []
    for row in source[:100] if isinstance(source, list) else []:
        if not isinstance(row, dict):
            continue
        day = row.get("day") or row.get("day_name") or row.get("weekday") or "Scheduled"
        period_value = row.get("period") or row.get("period_number") or row.get("slot")
        if period_value is None and row.get("period_index") is not None:
            period_value = f"P{int(row['period_index']) + 1}"
        period = period_value or "Period"
        subject = row.get("subject_name") or row.get("subject") or row.get("subject_code") or row.get("activity")
        if not subject:
            continue
        start, end = row.get("start_time"), row.get("end_time")
        display_time = " – ".join(value for value in (_display(start) if start else "", _display(end) if end else "") if value)
        entries.append({"day": _display(day), "period": _display(period), "subject": _display(subject),
                        "time": display_time or None, "room": _plain(row.get("room") or row.get("room_code"), 80) or None,
                        "faculty": _plain(row.get("faculty") or row.get("faculty_name"), 160) or None})
    if not entries:
        return "No timetable entries are currently available in this authorized scope.", [{
            "type": "empty_state", "title": "No timetable entries",
            "message": "No scheduled classes matched this request."}]
    current = data.get("current")
    summary = f"Your authorized timetable contains {len(entries)} scheduled entr{'y' if len(entries) == 1 else 'ies'}."
    if isinstance(current, dict) and current.get("subject_name"):
        summary = f"Your current class is {_plain(current['subject_name'], 160)}."
    return summary, [{"type": "timetable", "title": "Timetable", "entries": entries}]


def _profile(data: dict) -> tuple[str, list[dict]]:
    safe = {key: value for key, value in data.items() if key in {
        "display_name", "role", "department", "year", "semester", "section", "designation"}}
    cards = [{"label": key.replace("_", " ").title(), "value": _display(value), "tone": "neutral"}
             for key, value in safe.items()]
    name = _plain(safe.get("display_name"), 120)
    return (f"This is the safe profile for {name}." if name else "Here is your safe authenticated profile."), [{
        "type": "metric_cards", "title": "Profile", "cards": cards or [
            {"label": "Profile", "value": "Unavailable", "tone": "neutral"}]}]


def _hall_ticket(data: dict) -> tuple[str, list[dict]]:
    eligible = bool(data.get("eligible"))
    reasons = [_plain(value, 300) for value in data.get("reasons", [])[:10] if _plain(value, 300)]
    summary = "You are currently eligible for a hall ticket." if eligible else "You are not currently eligible for a hall ticket."
    blocks: list[dict] = [{"type": "status_notice", "title": "Hall-ticket eligibility",
                          "message": summary, "status": "success" if eligible else "warning"}]
    if reasons:
        blocks.append({"type": "bullet_list", "title": "Eligibility details", "items": reasons})
    return summary, blocks


def _scholarship(data: dict) -> tuple[str, list[dict]]:
    state = _plain(data.get("state") or data.get("status"), 80) or "Unavailable"
    count = max(0, int(data.get("eligible_count") or 0))
    total = max(0, int(data.get("total_available") or 0))
    summary = f"Scholarship status: {state.replace('_', ' ').title()}."
    return summary, [{"type": "status_notice", "title": "Scholarship status", "message": summary,
                      "status": "success" if state == "ELIGIBLE" else "info" if state == "APPLIED" else "warning"},
                     {"type": "metric_cards", "title": "Scholarship overview", "cards": [
                         {"label": "Eligible opportunities", "value": str(count), "tone": "success" if count else "neutral"},
                         {"label": "Available", "value": str(total), "tone": "info"},
                     ]}]


def _notifications(data: dict) -> tuple[str, list[dict]]:
    notifications = data.get("notifications") if isinstance(data.get("notifications"), list) else []
    rows = []
    for item in notifications[:30]:
        if not isinstance(item, dict):
            continue
        rows.append({"title": _plain(item.get("title"), 256), "message": _plain(item.get("message"), 600),
                     "type": _plain(item.get("notification_type"), 80),
                     "received": _display(item.get("created_at") or item.get("at")),
                     "status": "Read" if item.get("read") else "Unread"})
    if not rows:
        return "You have no notifications to show.", [{"type": "empty_state", "title": "No notifications",
                                                        "message": "There are no notifications in your account."}]
    unread = sum(row["status"] == "Unread" for row in rows)
    return f"You have {len(rows)} recent notification{'s' if len(rows) != 1 else ''}, including {unread} unread.", [{
        "type": "table", "title": "Recent notifications", "caption": "Notifications addressed to your account",
        "columns": [{"key": "title", "label": "Title"}, {"key": "message", "label": "Message"},
                    {"key": "type", "label": "Type"}, {"key": "received", "label": "Received"},
                    {"key": "status", "label": "Status"}], "rows": rows}]


def _analytics(result: dict, data: dict) -> tuple[str, list[dict]]:
    """Render only allowlisted aggregate DTO fields; never expose row identity."""
    summary = _plain(result.get("text"), 1200) or "Here is the authorized analytics result."
    kind = str(data.get("analytics_kind") or result.get("intent") or "")
    placement = kind in {"get_department_placement_summary",
                         "get_department_placed_student_count",
                         "get_department_offer_count"}
    if placement:
        available = bool(data.get("offer_data_available"))
        cards = [
            {"label": "Total students", "value": str(data.get("student_count", 0)), "tone": "neutral"},
            {"label": "Placement-eligible students", "value": str(data.get("placement_eligible_students", 0)), "tone": "info"},
            {"label": "Students with confirmed offers",
             "value": str(data.get("students_with_confirmed_offers", 0)) if available else "Unavailable",
             "tone": "success" if available and data.get("students_with_confirmed_offers") else "warning" if not available else "neutral"},
            {"label": "Total offers", "value": str(data.get("total_offers", 0)) if available else "Unavailable",
             "tone": "success" if available and data.get("total_offers") else "warning" if not available else "neutral"},
            {"label": "Students without an offer",
             "value": str(data.get("students_without_offer", 0)) if available else "Unavailable", "tone": "neutral"},
        ]
        blocks: list[dict] = [{"type": "metric_cards", "title": "Placement analytics", "cards": cards}]
        source_rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        table_rows = []
        if data.get("institution_scope"):
            columns = [{"key": "department", "label": "Department"},
                       {"key": "students", "label": "Total students"},
                       {"key": "eligible", "label": "Placement-eligible"},
                       {"key": "placed", "label": "Students with offers"},
                       {"key": "offers", "label": "Total offers"},
                       {"key": "without_offer", "label": "Without an offer"}]
            for row in source_rows:
                table_rows.append({"department": _plain(row.get("department_code"), 20),
                                   "students": int(row.get("student_count") or 0),
                                   "eligible": int(row.get("placement_eligible_students") or 0),
                                   "placed": (int(row.get("students_with_confirmed_offers") or 0)
                                              if row.get("offer_data_available") else "Unavailable"),
                                   "offers": (int(row.get("total_offers") or 0)
                                              if row.get("offer_data_available") else "Unavailable"),
                                   "without_offer": (int(row.get("students_without_offer") or 0)
                                                     if row.get("offer_data_available") else "Unavailable")})
        else:
            columns = [{"key": "company", "label": "Company"},
                       {"key": "role", "label": "Drive / role"},
                       {"key": "students", "label": "Students with offers"},
                       {"key": "offers", "label": "Total offers"}]
            for row in source_rows:
                table_rows.append({"company": _plain(row.get("company"), 128),
                                   "role": _plain(row.get("role"), 128),
                                   "students": int(row.get("students_with_offers") or 0),
                                   "offers": int(row.get("offer_count") or 0)})
        if table_rows:
            blocks.append({"type": "table", "title": "Placement breakdown",
                           "caption": "Aggregate placement values within the authenticated scope",
                           "columns": columns, "rows": table_rows[:100]})
        elif not available:
            blocks.append({"type": "empty_state", "title": "Offer records unavailable",
                           "message": "The current database does not provide confirmed placement-offer records."})
        elif not data.get("placement_records_available"):
            blocks.append({"type": "empty_state", "title": "No placement records",
                           "message": "No placement records are available in this authorized scope."})
        else:
            blocks.append({"type": "empty_state", "title": "No confirmed offers",
                           "message": "No confirmed placement offers are recorded in this authorized scope yet."})
        return summary, blocks
    institution = kind in {"get_institution_overview", "get_institution_department_overview",
                           "get_institution_attendance_summary"}
    code = _plain(data.get("department_code"), 20) or "Institution"
    cards: list[dict] = []
    if institution:
        cards = [
            {"label": "Departments", "value": str(data.get("department_count", 0)), "tone": "info"},
            {"label": "Students", "value": str(data.get("student_count", 0)), "tone": "neutral"},
        ]
        if "faculty_count" in data:
            cards.append({"label": "Faculty", "value": str(data.get("faculty_count", 0)), "tone": "neutral"})
        if kind == "get_institution_attendance_summary":
            cards.extend([
                {"label": "Students included", "value": str(data.get("students_included", 0)), "tone": "neutral"},
                {"label": "Average attendance", "value": f"{float(data.get('average_attendance') or 0):.1f}%",
                 "tone": "warning" if float(data.get("average_attendance") or 0) < 75 else "success"},
            ])
    elif kind == "get_department_faculty_count":
        cards = [
            {"label": "Department", "value": code, "tone": "info"},
            {"label": "Faculty", "value": str(data.get("faculty_count", 0)), "tone": "neutral"},
        ]
    elif kind in {"get_department_student_count", "get_department_student_count_by_year"}:
        cards = [
            {"label": "Department", "value": code, "tone": "info"},
            {"label": "Students", "value": str(data.get("student_count", 0)), "tone": "neutral"},
        ]
        if data.get("year"):
            cards.append({"label": "Academic year", "value": str(data["year"]), "tone": "neutral"})
    elif kind == "get_department_average_cgpa":
        included = int(data.get("students_included") or 0)
        if not included:
            return summary, [{"type": "empty_state", "title": "No CGPA data",
                              "message": "No CGPA records matched the authorized department scope."}]
        cards = [
            {"label": "Department", "value": code, "tone": "info"},
            {"label": "Students included", "value": str(included), "tone": "neutral"},
            {"label": "Average CGPA", "value": f"{float(data.get('average_cgpa') or 0):.2f}", "tone": "neutral"},
        ]
    elif kind == "get_department_marks_summary":
        included = int(data.get("students_included") or 0)
        if not included:
            return summary, [{"type": "empty_state", "title": "No marks data",
                              "message": "No marks records matched the authorized department and academic filters."}]
        cards = [
            {"label": "Department", "value": code, "tone": "info"},
            {"label": "Students included", "value": str(included), "tone": "neutral"},
            {"label": "Average marks", "value": f"{float(data.get('average_marks') or 0):.1f}%", "tone": "neutral"},
        ]
    else:
        included = int(data.get("students_included") or 0)
        if kind != "get_department_overview" and not included:
            return summary, [{"type": "empty_state", "title": "No attendance data",
                              "message": "No attendance records matched the authorized department and academic filters."}]
        cards = [
            {"label": "Department", "value": code, "tone": "info"},
            {"label": "Students included", "value": str(included), "tone": "neutral"},
            {"label": "Average attendance", "value": f"{float(data.get('average_attendance') or 0):.1f}%",
             "tone": "warning" if float(data.get("average_attendance") or 0) < 75 else "success"},
            {"label": "Students below 75%", "value": str(data.get("students_below_75", 0)),
             "tone": "danger" if data.get("students_below_75") else "success"},
        ]
        if kind == "get_department_overview":
            cards.insert(1, {"label": "Students", "value": str(data.get("student_count", 0)), "tone": "neutral"})
            cards.insert(2, {"label": "Faculty", "value": str(data.get("faculty_count", 0)), "tone": "neutral"})
            cards.append({"label": "Average marks", "value": f"{float(data.get('average_marks') or 0):.1f}%",
                          "tone": "neutral"})
    blocks: list[dict] = [{"type": "metric_cards", "title": "Authorized analytics", "cards": cards}]
    source_rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    rows = []
    if institution:
        columns = [{"key": "department", "label": "Department"},
                   {"key": "students", "label": "Students"}]
        if source_rows and "faculty_count" in source_rows[0]:
            columns.append({"key": "faculty", "label": "Faculty"})
        columns.extend([{"key": "average_attendance", "label": "Average attendance"},
                        {"key": "below_75", "label": "Below 75%"}])
        if source_rows and "average_cgpa" in source_rows[0]:
            columns.append({"key": "average_cgpa", "label": "Average CGPA"})
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            clean = {"department": _plain(row.get("department_code"), 20),
                     "students": int(row.get("student_count") or 0),
                     "average_attendance": f"{float(row.get('average_attendance') or 0):.1f}%",
                     "below_75": int(row.get("students_below_75") or 0)}
            if "faculty_count" in row:
                clean["faculty"] = int(row.get("faculty_count") or 0)
            if "average_cgpa" in row:
                clean["average_cgpa"] = f"{float(row.get('average_cgpa') or 0):.2f}"
            rows.append(clean)
    elif source_rows:
        first = source_rows[0]
        if "subject_code" in first:
            group_columns = [{"key": "subject_code", "label": "Subject code"},
                             {"key": "subject", "label": "Subject"}]
            group_keys = ("subject_code", "subject")
        elif "section" in first:
            group_columns = [{"key": "section", "label": "Section"}]
            group_keys = ("section",)
        else:
            group_columns = [{"key": "semester", "label": "Semester"}]
            group_keys = ("semester",)
        mark_rows = "average_marks" in first
        columns = [*group_columns,
                   {"key": "students_included", "label": "Students included"},
                   {"key": "average", "label": "Average marks" if mark_rows else "Average attendance"}]
        if not mark_rows:
            columns.append({"key": "below_75", "label": "Below 75%"})
        for row in source_rows:
            clean = {key: _display(row.get(key)) for key in group_keys}
            clean.update(students_included=int(row.get("students_included") or 0),
                         average=f"{float(row.get('average_marks' if mark_rows else 'average_attendance') or 0):.1f}%")
            if not mark_rows:
                clean["below_75"] = int(row.get("students_below_75") or 0)
            rows.append(clean)
    if rows:
        blocks.append({"type": "table", "title": "Analytics breakdown",
                       "caption": "Aggregate values within the authenticated scope",
                       "columns": columns, "rows": rows[:100]})
    return summary, blocks


def _general_blocks(text: str) -> list[dict]:
    clean = _plain(text, 6000)
    if not clean:
        return [{"type": "empty_state", "title": "No answer available",
                 "message": "The assistant could not produce a safe answer."}]
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    blocks, prose, bullets = [], [], []
    pending_heading = None

    def flush_prose():
        nonlocal pending_heading
        if prose:
            block = {"type": "text", "content": "\n".join(prose)}
            if pending_heading:
                block["heading"] = pending_heading
                pending_heading = None
            blocks.append(block)
            prose.clear()

    def flush_bullets():
        nonlocal pending_heading
        if bullets:
            block = {"type": "bullet_list", "items": bullets[:]}
            if pending_heading:
                block["title"] = pending_heading
                pending_heading = None
            blocks.append(block)
            bullets.clear()

    for line in lines:
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            flush_bullets()
            flush_prose()
            pending_heading = _plain(heading.group(1), 120)
            continue
        bullet = re.match(r"^(?:[-*•]|\d+[.)])\s+(.+)$", line)
        if bullet:
            flush_prose()
            bullets.append(_plain(bullet.group(1), 600))
        else:
            flush_bullets()
            prose.append(line)
    flush_bullets()
    flush_prose()
    if pending_heading:
        blocks.append({"type": "text", "heading": pending_heading, "content": pending_heading})
    return blocks[:12] or [{"type": "text", "content": clean}]


def _suggestions(intent: str, category: str, role: str) -> list[str]:
    studentish = role in {"student", "parent"}
    mapping = {
        "department_average_attendance": ["Show attendance risk by semester", "Show student count in my department", "Show average marks for first year"],
        "department_attendance": ["Show department attendance summary", "Show student count in my department", "Show average marks for first year"],
        "department_student_count": ["Show department attendance summary", "Show faculty count in my department", "Show attendance risk by semester"],
        "department_faculty_count": ["Show student count in my department", "Show department attendance summary", "Show department overview"],
        "department_marks": ["Show department attendance summary", "Show attendance risk by semester", "Show student count in my department"],
        "department_overview": ["Show attendance risk by semester", "Show student count in my department", "Show average marks for first year"],
        "institution_overview": ["Show institution overview", "What can you help me with?", "What is MAWOS?"],
        "attendance": ["Show my internal marks", "Show hall-ticket eligibility", "Show my timetable"],
        "marks": ["Show my attendance", "Show hall-ticket eligibility", "Explain how CIE works"],
        "fee": ["Show hall-ticket eligibility", "Show my profile", "What can you help me with?"],
        "hall_ticket": ["Show my attendance", "Show my fee status", "Show my internal marks"],
        "placement": ["Show shortlisted companies", "Show placement details", "Show campus events"],
        "library": ["Recommend a Python book", "Check another book's availability", "Open library catalogue"],
        "timetable": ["Show campus events", "Show my notifications", "Show my attendance"],
        "event": ["Show my timetable", "Show my notifications", "What can you help me with?"],
        "scholarship": ["Show scholarship details", "Show my fee status", "Show my notifications"],
        "notification": ["Show campus events", "Show my timetable", "Show my placements"],
        "profile": ["Show my attendance", "Show my internal marks", "What can you help me with?"],
    }
    lowered = intent.casefold()
    chosen = next((values for key, values in mapping.items() if key in lowered), None)
    if chosen is None and category == "library_catalogue":
        chosen = mapping["library"]
    if chosen is None and category == "general_ai":
        chosen = ["Explain that more simply", "Give me a short example", "What should I learn next?"]
    if chosen is None:
        chosen = ["What can you help me with?", "What is MAWOS?", "Explain machine learning simply"]
    analytics_suggestions = "department_" in lowered or "institution_" in lowered
    if role == "parent" and not analytics_suggestions:
        chosen = [item.replace("my ", "my child's ") for item in chosen]
    if not studentish and not analytics_suggestions:
        chosen = [item for item in chosen if not re.search(r"\bmy (attendance|fee|marks|timetable|placements)", item, re.I)]
        chosen += ["What can you help me with?", "What is MAWOS?"]
    return list(dict.fromkeys(chosen))[:4] if len(set(chosen)) >= 2 else [*chosen, "What is MAWOS?"][:2]


def _safe_trace(result: dict, role: str, duration_ms: float, intent: str) -> dict | None:
    if role in {"student", "parent"}:
        return {"visibility": "simple", "summary": "Verified against your authorized MAWOS records."
                if result.get("tools_used") or result.get("data") else "Prepared within your authorized MAWOS scope.",
                "steps": [], "duration_ms": round(max(0, duration_ms), 1)}
    if role not in {"admin", "hod", "principal", "faculty"}:
        return None
    source = _source(result)
    classified_only = bool(result.get("routing", {}).get("attempted_llm")
                           and result.get("routing", {}).get("accepted_llm")
                           and source == "deterministic" and data_is_aggregate(result))
    provider_detail = ("Groq classified the aggregate intent; no database result was sent back"
                       if classified_only else
                       "Groq used for a general-learning explanation" if source == "groq" else
                       "Local AI used for a general-learning explanation" if source == "ollama" else
                       "AI provider bypassed for this deterministic request" if source == "deterministic" else
                       "Safe fallback used; no unverified record claim returned")
    intent_label = ("Department Strength query" if intent == "get_department_student_count"
                    else _plain(intent.replace("_", " ").title(), 120))
    steps = [
        {"label": "Intent recognized", "detail": intent_label, "status": "complete"},
        {"label": "Authorization", "detail": f"Verified for the authenticated {role} scope", "status": "complete"},
    ]
    if result.get("tools_used") or result.get("data"):
        steps.append({"label": "Records", "detail": "Authorized read-only records checked", "status": "complete"})
    steps.append({"label": "Provider", "detail": provider_detail,
                  "status": "safe_fallback" if source == "safe_fallback" else "bypassed" if source == "deterministic" else "complete"})
    return {"visibility": "collapsible", "summary": "Safe routing and authorization details",
            "steps": steps, "duration_ms": round(max(0, duration_ms), 1)}


def data_is_aggregate(result: dict) -> bool:
    data = result.get("data")
    return isinstance(data, dict) and bool(data.get("analytics_kind"))


def _legacy_actions(result: dict) -> list[dict]:
    blocks = []
    for action in result.get("actions", [])[:5] if isinstance(result.get("actions"), list) else []:
        if not isinstance(action, dict):
            continue
        try:
            link = LinkActionBlock(type="link_action", label=_plain(action.get("label"), 100),
                                   url=str(action.get("route") or ""), external=False)
        except ValidationError:
            continue
        blocks.append(link.model_dump())
    return blocks


def structure_response(result: dict, *, role: str, duration_ms: float) -> dict:
    """Return a ChatResponse-valid projection of an existing assistant result."""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    intent = _plain(result.get("intent") or result.get("context_topic") or result.get("category") or "assistant", 120)
    category = str(result.get("category") or "unsupported")
    denied = "error" in data or _source(result) == "safe_fallback"

    if denied:
        detail = _plain(result.get("text"), 1200) or "This request could not be completed safely."
        summary = "This request was not completed."
        block_type = "warning" if category == "sensitive_or_disallowed" else "status_notice"
        blocks = ([{"type": "warning", "title": "Request not completed", "message": detail}]
                  if block_type == "warning" else
                  [{"type": "status_notice", "title": "Safe assistant response", "message": detail,
                    "status": "warning"}])
    elif data.get("analytics_kind"):
        summary, blocks = _analytics(result, data)
    elif "attendance" in intent:
        summary, blocks = _attendance(data)
    elif "marks" in intent:
        summary, blocks = _marks(data)
    elif "fee" in intent:
        summary, blocks = _fees(data)
    elif "placement" in intent:
        summary, blocks = _placements(data)
    elif "library" in intent or category == "library_catalogue":
        summary, blocks = _library(data)
    elif "event" in intent:
        summary, blocks = _events(data)
    elif "timetable" in intent:
        summary, blocks = _timetable(data)
    elif "scholarship" in intent:
        summary, blocks = _scholarship(data)
    elif "notification" in intent:
        summary, blocks = _notifications(data)
    elif "profile" in intent and data:
        summary, blocks = _profile(data)
    elif "hall_ticket" in intent or "eligibility" in intent:
        summary, blocks = _hall_ticket(data)
    elif category == "department_record" and data:
        summary = _plain(result.get("text"), 1200)
        blocks = [{"type": "metric_cards", "title": "Authorized department summary", "cards": [
            {"label": "Students", "value": str(data.get("student_count", 0)), "tone": "info"},
            {"label": "Faculty", "value": str(data.get("faculty_count", 0)), "tone": "neutral"},
        ]}]
    else:
        clean = _plain(result.get("text"), 6000)
        summary = ("Here is the general-learning answer." if category == "general_ai" else
                   "Here is the assistant response.")
        blocks = _general_blocks(clean)

    blocks.extend(_legacy_actions(result))
    result.update({
        "summary": summary,
        "blocks": blocks[:20],
        "suggestions": _suggestions(intent, category, role),
        "source": _source(result),
        "intent": intent,
        "safe_trace": _safe_trace(result, role, duration_ms, intent),
        "refresh_required": bool(result.get("refresh_required", False)),
    })
    # The legacy top-level action contract is intentionally library-only.
    # Other safe internal routes are represented by validated link_action
    # blocks and must not make the narrower compatibility model fail closed.
    result["actions"] = [action for action in result.get("actions", [])
                         if isinstance(action, dict) and re.fullmatch(
                             r"/student/library(?:\?q=[^\s]*)?",
                             str(action.get("route") or ""))]
    # Validate here as a fail-closed boundary before FastAPI serialization.
    # Legacy orchestrators occasionally carry internal-only keys; never expose
    # them merely because they were present in an intermediate dictionary.
    public = {key: value for key, value in result.items() if key in ChatResponse.model_fields}
    try:
        return ChatResponse.model_validate(public).model_dump()
    except ValidationError:
        fallback_text = "The assistant result could not be displayed safely. Please retry your request."
        fallback = {
            "summary": fallback_text,
            "blocks": [{"type": "status_notice", "title": "Safe fallback",
                        "message": fallback_text, "status": "warning"}],
            "suggestions": ["Try that request again", "What can you help me with?"],
            "source": "safe_fallback", "intent": intent or "assistant",
            "safe_trace": _safe_trace({"fallback": True}, role, duration_ms, intent or "assistant"),
            "refresh_required": False,
            "text": fallback_text, "category": "unsupported", "source_label": "Safe fallback",
            "mode": "scope", "routing": {
                "tier": "scope", "margin": 0.0, "tau": 0.0, "escalated": False,
                "attempted_llm": False, "accepted_llm": False,
                "deterministic_fallback": True, "reason": "structured response validation failed",
                "fallback_from": None,
            },
            "tools_used": [], "knowledge_sources": [], "latency_ms": round(max(0, duration_ms), 1),
            "fallback": True, "fallback_code": "invalid_structured_response", "actions": [],
            "context_books": [],
        }
        return ChatResponse.model_validate(fallback).model_dump()
