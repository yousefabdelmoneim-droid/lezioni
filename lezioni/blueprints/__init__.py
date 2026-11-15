def register_blueprints(app):
    from . import api, auth, dashboard, groups, lessons, payments, reports, settings, students

    for module in (auth, dashboard, students, groups, lessons, payments, reports, settings, api):
        app.register_blueprint(module.bp)
