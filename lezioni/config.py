import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

_DEV_SECRET = "dev-only-secret-change-me"


def _database_url():
    url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}")
    # Neon / Heroku style URLs
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def is_production():
    """Render sets RENDER=true on every service."""
    return os.environ.get("RENDER") == "true" or os.environ.get("FLASK_ENV") == "production"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_SECRET)
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = is_production()
    REMEMBER_COOKIE_SECURE = is_production()
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    WTF_CSRF_TIME_LIMIT = None  # token valid for the whole session
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    # Run migrations automatically on boot (single gunicorn worker on Render).
    AUTO_MIGRATE = os.environ.get("AUTO_MIGRATE", "1") == "1"

    # First account, created only when the users table is empty.
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

    TIMEZONE = "Europe/Rome"

    # Origins allowed to call /api/v1 from a browser engine (the Android app's
    # WebView is https://localhost; add http://localhost:5173 for development).
    API_CORS_ORIGINS = [
        o.strip()
        for o in os.environ.get("API_CORS_ORIGINS", "https://localhost,capacitor://localhost,http://localhost").split(",")
        if o.strip()
    ]


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    AUTO_MIGRATE = False
    ADMIN_USERNAME = None
    ADMIN_PASSWORD = None
