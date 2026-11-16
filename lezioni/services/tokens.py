"""Bearer tokens for the mobile app.

The plain token is shown once (at login) and stored on the phone in the
Android Keystore; the database keeps only its SHA-256 hash.
"""
import hashlib
import secrets
from datetime import timedelta

from ..extensions import db
from ..models import ApiToken, as_utc, utcnow

PREFIX = "lzn_"
LIFETIME = timedelta(days=90)
TOUCH_EVERY = timedelta(minutes=10)


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(user, name=""):
    token = PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(user_id=user.id, token_hash=_hash(token), name=(name or "")[:120],
                   expires_at=utcnow() + LIFETIME)
    db.session.add(row)
    db.session.commit()
    return token, row


def lookup(token):
    """Return the active ApiToken for a plain token, or None."""
    if not token or not token.startswith(PREFIX) or len(token) > 200:
        return None
    row = ApiToken.query.filter_by(token_hash=_hash(token)).first()
    if row is None or row.revoked_at is not None:
        return None
    now = utcnow()
    if as_utc(row.expires_at) <= now:
        return None
    if row.last_used_at is None or now - as_utc(row.last_used_at) > TOUCH_EVERY:
        row.last_used_at = now
        db.session.commit()
    return row


def revoke(row):
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        db.session.commit()


def bearer_from(request):
    header = request.headers.get("Authorization", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip()
    return None
