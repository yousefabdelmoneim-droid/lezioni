import logging
import os

import click
from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .config import Config, _DEV_SECRET, is_production
from .extensions import csrf, db, login_manager, migrate

log = logging.getLogger(__name__)

PUBLIC_ENDPOINTS = {"auth.login", "auth.set_lang", "health", "static"}


@event.listens_for(Engine, "connect")
def _sqlite_fk_pragma(dbapi_conn, _record):
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    if is_production() and app.config["SECRET_KEY"] == _DEV_SECRET:
        raise RuntimeError("SECRET_KEY must be set in production.")
    if is_production():
        # Render terminates TLS in front of the app: trust exactly one proxy hop.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(app.root_path), "migrations"),
                     render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None

    from . import models  # noqa: F401  (register models)
    from .blueprints import register_blueprints
    from .templating import register_templating

    register_blueprints(app)
    register_templating(app)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(models.User, int(user_id))
        except (TypeError, ValueError):
            return None

    @app.route("/health")
    def health():
        return "ok", 200

    @app.before_request
    def _require_login():
        g.default_lang = None
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if not current_user.is_authenticated:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "auth"}), 401
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?") if request.method == "GET" else None))
        return None

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "form-action 'self'; base-uri 'self'",
        )
        if is_production():
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if current_user.is_authenticated and request.endpoint != "static":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.errorhandler(CSRFError)
    def _csrf_error(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "csrf"}), 400
        return render_template("errors/error.html", code=400, message_key="err_csrf"), 400

    @app.errorhandler(404)
    def _not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "not_found"}), 404
        return render_template("errors/error.html", code=404, message_key="err_404"), 404

    @app.errorhandler(500)
    def _server_error(e):
        db.session.rollback()
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "server"}), 500
        return render_template("errors/error.html", code=500, message_key="err_500"), 500

    register_cli(app)

    if app.config.get("AUTO_MIGRATE"):
        from .boot import ensure_schema

        ensure_schema(app)
        bootstrap_admin(app)

    return app


def bootstrap_admin(app):
    """Create the first account from ADMIN_USERNAME / ADMIN_PASSWORD if there is none."""
    from .models import User

    username = app.config.get("ADMIN_USERNAME")
    password = app.config.get("ADMIN_PASSWORD")
    with app.app_context():
        if User.query.count() > 0 or not username or not password:
            return
        if len(password) < 8:
            log.error("ADMIN_PASSWORD must be at least 8 characters; no account created.")
            return
        user = User(username=username.strip().lower(), display_name=username.strip())
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        log.warning("Created first account %r from environment variables.", user.username)


def register_cli(app):
    @app.cli.command("create-user")
    @click.argument("username")
    @click.password_option()
    def create_user(username, password):
        """Create a login account."""
        from .models import User

        if User.query.filter_by(username=username.lower()).first():
            raise click.ClickException("User already exists")
        u = User(username=username.lower(), display_name=username)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Created {username}")

    @app.cli.command("set-password")
    @click.argument("username")
    @click.password_option()
    def set_password(username, password):
        """Reset an account password."""
        from .models import User

        u = User.query.filter_by(username=username.lower()).first()
        if not u:
            raise click.ClickException("No such user")
        u.set_password(password)
        u.failed_logins = 0
        u.locked_until = None
        db.session.commit()
        click.echo("Password updated")

    @app.cli.command("migrate-db")
    def migrate_db():
        """Apply database migrations safely (same as on boot)."""
        from .boot import ensure_schema

        ensure_schema(app)
        click.echo("Database is up to date")
