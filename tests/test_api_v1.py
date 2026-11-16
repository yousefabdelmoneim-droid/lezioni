"""Mobile API: token auth, CORS, and the same business rules as the website."""
from datetime import timedelta

import pytest

from lezioni.extensions import db
from lezioni.models import ApiToken, Payment, ScheduleSlot
from lezioni.services import attendance

from . import factories as f

ORIGIN = "https://localhost"


@pytest.fixture()
def token(client):
    r = client.post("/api/v1/auth/login", json={"username": "papa", "password": "correct-horse", "device_name": "Pixel"})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]


@pytest.fixture()
def api(client, token):
    class Api:
        def __getattr__(self, method):
            def call(url, **kw):
                headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN}
                return getattr(client, method)("/api/v1" + url, headers=headers, **kw)
            return call
    return Api()


@pytest.fixture()
def data(app, today):
    with app.app_context():
        a = f.student("أحمد علي", guardian_phone="347 1112223", message_language="ar")
        b = f.student("Sara Bianchi", billing_mode="prepaid")
        g = f.group("رياضيات", members=[a, b])
        db.session.add(ScheduleSlot(group_id=g.id, weekday=today.weekday(), start_time="17:00",
                                    valid_from=today - timedelta(days=30)))
        db.session.commit()
        past = f.lesson(on=today - timedelta(days=7), group=g, homework="تمارين صفحة 12")
        attendance.mark(past, [{"student_id": a.id, "status": "present"}, {"student_id": b.id, "status": "absent"}])
        return {"a": a.id, "b": b.id, "g": g.id, "lesson": past.id}


def test_login_errors_and_token_storage(client, app):
    assert client.post("/api/v1/auth/login", json={"username": "papa", "password": "x"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"username": "papa", "password": "correct-horse"})
    tok = r.get_json()["token"]
    assert tok.startswith("lzn_")
    with app.app_context():
        row = ApiToken.query.one()
        assert tok not in row.token_hash and len(row.token_hash) == 64  # only the hash is stored


def test_requires_bearer_and_rejects_revoked(client, api):
    assert client.get("/api/v1/dashboard").status_code == 401
    assert client.get("/api/v1/dashboard", headers={"Authorization": "Bearer lzn_wrong"}).status_code == 401
    assert api.get("/dashboard").status_code == 200
    assert api.post("/auth/logout").status_code == 200
    assert api.get("/dashboard").status_code == 401


def test_bearer_not_accepted_outside_api(client, token):
    r = client.get("/students/", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 302  # website still needs its own login


def test_expired_token(client, app, token):
    with app.app_context():
        row = ApiToken.query.one()
        row.expires_at = row.created_at - timedelta(days=1)
        db.session.commit()
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_cors_preflight_and_headers(client, api):
    r = client.options("/api/v1/students", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                                                    "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
    r = client.options("/api/v1/students", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers
    assert api.get("/me").headers["Access-Control-Allow-Origin"] == ORIGIN


def test_sync_bundle(api, data):
    r = api.get("/sync").get_json()
    assert r["ok"] and {s["name"] for s in r["students"]} == {"أحمد علي", "Sara Bianchi"}
    assert any(i["virtual"] for i in r["agenda"]["items"])
    assert r["settings"]["policy"]["excused"] is False


def test_student_profile_and_history(api, data):
    r = api.get(f"/students/{data['a']}").get_json()
    assert r["student"]["account"]["consumed"] == 1
    assert r["whatsapp"]["homework"].startswith("https://wa.me/393471112223?text=")
    hist = api.get(f"/students/{data['a']}/lessons").get_json()["history"]
    assert hist[0]["attendance"]["consumed"] is True


def test_create_and_update_student(api):
    r = api.post("/students", json={"name": "Luca", "billing_mode": "prepaid", "price_per_lesson_cents": 2000})
    assert r.status_code == 201
    sid = r.get_json()["student"]["id"]
    assert api.post("/students", json={"name": "luca"}).status_code == 409
    r = api.patch(f"/students/{sid}", json={"guardian_phone": "333 1234567"})
    assert r.get_json()["student"]["guardian_phone"] == "333 1234567"


def test_attendance_and_notebook(api, data, today):
    r = api.post("/lessons/open-slot", json={"slot_id": 1, "date": today.isoformat()})
    lesson = r.get_json()["lesson"]
    r = api.put(f"/lessons/{lesson['id']}/attendance", json={"entries": [
        {"student_id": data["a"], "status": "present", "performance": 5, "homework_status": "done"},
        {"student_id": data["b"], "status": "excused"}]})
    people = {p["student"]["id"]: p for p in r.get_json()["participants"]}
    assert people[data["a"]]["attendance"]["consumed"] is True
    assert people[data["b"]]["attendance"]["consumed"] is False
    v = lesson["version"]
    assert api.patch(f"/lessons/{lesson['id']}/notebook", json={"version": v, "topics": "الكسور"}).get_json()["version"] == v + 1
    r = api.patch(f"/lessons/{lesson['id']}/notebook", json={"version": v, "topics": "old"})
    assert r.status_code == 409 and r.get_json()["server"]["topics"] == "الكسور"


def test_collect_is_idempotent(api, app, data):
    body = {"mode": "lessons", "lessons": 2, "unit_price_cents": 2000, "amount_paid_cents": 4000,
            "method": "cash", "idempotency_key": "phone-key-1"}
    r1, r2 = api.post(f"/students/{data['a']}/collect", json=body), api.post(f"/students/{data['a']}/collect", json=body)
    assert r1.status_code == 201 and r2.status_code == 200 and r2.get_json()["created"] is False
    with app.app_context():
        assert Payment.query.count() == 1
    assert api.post(f"/students/{data['a']}/collect", json={"mode": "payment_only", "idempotency_key": "k"}).status_code == 422


def test_lesson_conflict_and_pdf(api, data, today):
    r = api.post("/lessons", json={"student_id": data["a"], "date": today.isoformat(), "time": "17:30"})
    assert r.status_code == 409 and r.get_json()["conflicts"]
    r = api.post("/lessons", json={"student_id": data["a"], "date": today.isoformat(), "time": "17:30",
                                   "confirm_conflict": True})
    assert r.status_code == 201
    pdf = api.get(f"/pdf/students/{data['a']}/report?lang=ar")
    assert pdf.status_code == 200 and pdf.data[:5] == b"%PDF-"
    assert api.get("/pdf/students/9999/report").status_code == 404


def test_validation_errors_are_json(api):
    r = api.post("/students", data="not json", content_type="application/json")
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_json"
    r = api.post("/lessons", json={"date": "2026-02-30", "time": "25:00", "student_id": 1})
    assert r.get_json()["error"] == "invalid_datetime"


def test_groups_and_assessments(api, data):
    r = api.post("/groups", json={"name": "Fisica", "subject": "فيزياء", "color": "#123456"})
    assert r.status_code == 201
    gid = r.get_json()["group"]["id"]
    assert api.post("/groups", json={"name": "fisica"}).status_code == 409
    assert api.post(f"/groups/{gid}/members", json={"student_ids": [data["a"], data["b"]]}).get_json()["added"] == 2
    members = api.get(f"/groups/{gid}").get_json()["members"]
    assert api.delete(f"/groups/members/{members[0]['membership_id']}").status_code == 200
    assert len(api.get(f"/groups/{gid}").get_json()["members"]) == 1
    assert api.patch(f"/groups/{gid}", json={"active": False}).get_json()["group"]["active"] is False
    r = api.post(f"/students/{data['a']}/assessments", json={"title": "Verifica", "score": 8, "max_score": 10})
    aid = r.get_json()["assessment"]["id"]
    assert r.get_json()["assessment"]["percent"] == 80.0
    assert api.delete(f"/assessments/{aid}").status_code == 200
    assert api.get(f"/students/{data['a']}/assessments").get_json()["assessments"] == []
