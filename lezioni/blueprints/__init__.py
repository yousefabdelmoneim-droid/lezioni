def register_blueprints(app):
    from ..extensions import csrf
    from . import api, api_v1, auth, dashboard, groups, lessons, payments, reports, settings, students

    for module in (auth, dashboard, students, groups, lessons, payments, reports, settings, api):
        app.register_blueprint(module.bp)
    # Mobile API: Bearer-token auth (no cookies), so CSRF does not apply.
    csrf.exempt(api_v1.bp)
    app.register_blueprint(api_v1.bp)
