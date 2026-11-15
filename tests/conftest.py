import os
import tempfile
from datetime import timedelta

import pytest

from lezioni import create_app
from lezioni.boot import ensure_schema
from lezioni.config import TestConfig
from lezioni.extensions import db as _db
from lezioni.models import Settings, User
from lezioni.utils import today_rome


def _make_app(db_url):
    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = db_url

    app = create_app(Cfg)
    ensure_schema(app)
    return app


@pytest.fixture()
def app():
    pg = os.environ.get("TEST_DATABASE_URL")
    if pg:
        url = pg
    else:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        url = f"sqlite:///{path}"
    app = _make_app(url)
    with app.app_context():
        s = Settings.get()
        s.default_price_cents = 2000
        u = User(username="papa", display_name="Papà")
        u.set_password("correct-horse")
        _db.session.add(u)
        _db.session.commit()
    yield app
    with app.app_context():
        _db.session.remove()
        if pg:
            _db.drop_all()
            _db.session.execute(_db.text("DROP TABLE IF EXISTS alembic_version"))
            _db.session.commit()
        _db.engine.dispose()
    if not pg:
        os.unlink(path)


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    r = client.post("/login", data={"username": "papa", "password": "correct-horse"})
    assert r.status_code == 302
    return client


@pytest.fixture()
def today():
    return today_rome()


@pytest.fixture()
def yesterday(today):
    return today - timedelta(days=1)
