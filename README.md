# Lezioni — gestione lezioni private / إدارة الدروس الخصوصية

A web app for a private teacher to manage students, groups, lessons, attendance,
lesson credits, payments and reports. Arabic (right-to-left) by default, with
Italian available from the menu.

Stack: Flask 3 · SQLAlchemy 2 · Alembic (Flask-Migrate) · Neon PostgreSQL · Render.
No JavaScript build step and no CDNs: fonts, calendar and icons are self-hosted.

## What it does

| Area | Features |
| --- | --- |
| Students | Profile (phones, guardian, grade, school, status, notes), search, filters (status, group, owing, overdue, low balance), sorting, archive, quick "lesson now" |
| Groups & schedule | Groups with members (join/leave history), weekly recurring slots, agenda (day/week), month/week/list calendar, conflict warnings, reschedule, cancel with reason |
| Lesson notebook | Title, topics, detailed notes, homework, observations, autosave; previous lesson's homework shown |
| Attendance | Present, late, absent, excused, makeup, cancelled; "everyone present" in one tap; homework done, 1–5 performance, note per student; guests/makeups from other groups |
| Credits & payments | Separate lesson-credit and money ledgers, packages, price per student, partial payments, outstanding and overdue, receipts, void with reason (no deletes), manual adjustments, duplicate-submission protection |
| Reports (PDF, Arabic shaping) | Receipt, student report, payment statement, monthly attendance sheet, group report, lesson summary, monthly financial summary; CSV exports |
| WhatsApp | Click-to-chat links with prefilled messages (payment, balance, absence, next lesson, homework) in the student's language |
| Security | Login, CSRF, account lockout, security headers/CSP, audit log of every change |

## Business rules (payments)

* **Remaining lessons = purchased − consumed ± adjustments.** Buy 8, attend 5 chargeable lessons → 3 remaining.
* **Outstanding = charged − paid.** Partial payments are allowed.
* Attendance consumes a credit according to the policy in *Settings* (default: present, late,
  absent and makeup consume; excused does not; cancelled and free lessons never do).
* A unique database index allows **at most one active consumption per attendance**, so a lesson
  is never deducted twice. Status changes add a reversal entry; nothing is edited in place.
* Every payment form carries a one-time key with a unique constraint: double taps save once.
* Money is stored as integer cents. Wrong payments are **voided with a reason**, never deleted.
* Pay-after students go negative (e.g. −3 = three lessons to collect); prepaid students get a
  low-balance alert.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes (production) | Neon connection string. `postgres://` is accepted. Local default: SQLite `app.db` |
| `SECRET_KEY` | yes (production) | Session signing key. Render generates it. The app refuses to start on Render without it |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | first deploy | Creates the first login **only if no account exists**. Password ≥ 8 characters; change it later in *Settings → Account* |
| `AUTO_MIGRATE` | no (default `1`) | Run database migrations on boot. Set `0` to run them manually with `flask --app app migrate-db` |

## Database migrations (safe upgrade of the existing Neon data)

On boot the app runs `lezioni/boot.py`:

1. If the database has the original tables but no `alembic_version`, it stamps the baseline
   revision `0001_baseline` (the original schema).
2. It applies pending migrations in **one transaction** (PostgreSQL), under an advisory lock.
   If anything fails, the database is left exactly as it was.

The migrations only **add** tables, columns and indexes. No column is dropped or renamed and no
row is deleted. Old data is converted as follows (`0003_legacy_backfill`):

* each past lesson becomes a completed lesson with the student marked present;
* past lessons marked *pagato* become opening credits, so unpaid past lessons appear as owed;
* future lessons stay scheduled; their *pagato* flag (set by the old "Pagato" button bug) is not
  counted as a prepayment;
* `lesson_days` becomes weekly schedule slots; the `pagato` column is kept untouched.

## Backup and recovery

Before merging a release that contains migrations:

1. In the Neon console, create a **branch** of the production database (instant copy).
2. Merge; Render redeploys and migrates on boot.
3. If something is wrong, point `DATABASE_URL` back to the branch, or use Neon's point-in-time
   restore. Neon keeps history for the retention window of your plan.

For an extra off-site copy: `pg_dump "$DATABASE_URL" > lezioni-$(date +%F).sql` (monthly is enough).
CSV exports of students and payments are available under *Reports*.

## Hosting region

The Neon database is in Frankfurt (AWS eu-central-1). The web service should be in
Render's **Frankfurt** region too: a page makes 10–25 queries, and each query that
crosses the Atlantic adds about 90 ms. A Render service cannot change region, so moving
means creating a new service (free plan) from this repo with `render.yaml`
(*New → Blueprint*) or by hand with region Frankfurt, the same `DATABASE_URL`,
build command `pip install -r requirements.txt` and start command `gunicorn app:app`.
The new service gets a new `*.onrender.com` address: update bookmarks and the
keep-alive ping on `/health`, check the new service works, then suspend the old one.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export ADMIN_USERNAME=papa ADMIN_PASSWORD=password123   # first run only
flask --app app run --debug                             # SQLite app.db, migrated automatically
pytest                                                  # 69 tests (SQLite)
TEST_DATABASE_URL=postgresql://localhost/lezioni_test pytest   # same tests on PostgreSQL
```

Other commands: `flask --app app create-user NAME`, `flask --app app set-password NAME`.

## Project layout

```
app.py                  entry point (gunicorn app:app)
lezioni/
  __init__.py           app factory, security headers, error pages, CLI
  boot.py               safe migrations on boot
  models.py             database models
  services/             billing, attendance, schedule, whatsapp, pdf, stats, audit
  blueprints/           routes per area
  templates/ static/    UI (RTL-aware CSS, self-hosted fonts and FullCalendar)
  translations.py       Arabic + Italian strings
  pdf_fonts/            IBM Plex Sans Arabic (SIL Open Font License)
migrations/             Alembic revisions
tests/                  pytest suite
```
