"""Recipient-owned, database-backed in-app notifications."""
from collections.abc import Iterable

from .models import Notification, User, utcnow


def safe_internal_route(route: str | None) -> str | None:
    """Allow app-relative routes only (never protocols or protocol-relative URLs)."""
    if not route:
        return None
    route = route.strip()
    if not route.startswith("/") or route.startswith("//") or "\\" in route or any(ord(c) < 32 for c in route):
        return None
    return route[:512]


def user_ids_for_usns(db, usns: Iterable[str]) -> list[int]:
    normalized = {str(usn).strip().upper() for usn in usns if usn}
    if not normalized:
        return []
    return [user_id for user_id, in db.query(User.id).filter(
        User.role == "student", User.usn.in_(normalized)).all()]


def notify_users(db, user_ids: Iterable[int], *, title: str, message: str,
                 notification_type: str, source_agent: str, event_key: str,
                 route: str | None = None, related_entity_type: str | None = None,
                 related_entity_id: object | None = None) -> int:
    """Stage one owned row per recipient using one existence query and one flush.

    The unique recipient/event constraint is the final concurrency guard. Callers
    that mutate the related entity hold its normal workflow lock, so retries do
    not race this insert in PostgreSQL.
    """
    ids = sorted({int(value) for value in user_ids if value is not None})
    if not ids:
        return 0
    usn_by_id = {user_id: usn for user_id, usn in db.query(User.id, User.usn).filter(User.id.in_(ids)).all()}
    existing = {value for value, in db.query(Notification.recipient_user_id).filter(
        Notification.recipient_user_id.in_(ids), Notification.event_key == event_key).all()}
    rows = [Notification(
        recipient_user_id=user_id,
        usn=usn_by_id.get(user_id),
        title=title[:256],
        message=message,
        notification_type=notification_type[:64],
        route=safe_internal_route(route),
        related_entity_type=related_entity_type[:64] if related_entity_type else None,
        related_entity_id=str(related_entity_id)[:64] if related_entity_id is not None else None,
        event_key=event_key[:255],
        source_agent=source_agent[:32],
    ) for user_id in ids if user_id not in existing]
    db.add_all(rows)
    db.flush()
    return len(rows)


def notify_usns(db, usns: Iterable[str], **kwargs) -> int:
    return notify_users(db, user_ids_for_usns(db, usns), **kwargs)


def notify_role(db, role: str, *, dept: str | None = None, **kwargs) -> int:
    query = db.query(User.id).filter(User.role == role)
    if dept is not None:
        query = query.filter(User.dept_code == dept)
    return notify_users(db, (user_id for user_id, in query.all()), **kwargs)


def owned_query(db, user):
    return db.query(Notification).filter(Notification.recipient_user_id == user.id)


def serialize(row: Notification) -> dict:
    return {
        "id": row.id, "title": row.title, "message": row.message,
        "notification_type": row.notification_type, "route": safe_internal_route(row.route),
        "related_entity_type": row.related_entity_type,
        "related_entity_id": row.related_entity_id,
        "source_agent": row.source_agent, "created_at": row.created_at,
        "at": row.created_at, "read": bool(row.read), "read_at": row.read_at,
    }


def mark_read(row: Notification) -> None:
    if not row.read:
        row.read = True
        row.read_at = utcnow()
