import json

from flask_login import current_user

from ..extensions import db
from ..models import AuditLog


def log(action, entity, entity_id=None, summary="", student_id=None, data=None, user_id=None):
    """Add an audit row to the current session (committed with the caller's transaction)."""
    if user_id is None:
        try:
            user_id = current_user.id if current_user and current_user.is_authenticated else None
        except Exception:  # outside a request
            user_id = None
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        student_id=student_id,
        summary=(summary or "")[:255],
        data=json.dumps(data, default=str, ensure_ascii=False) if data else None,
    )
    db.session.add(entry)
    return entry
