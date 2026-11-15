from flask import Blueprint, render_template

from ..models import Settings
from ..services import stats

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    settings = Settings.get()
    data = stats.dashboard(settings)
    max_rev = max([c for _, c in data["revenue"]] + [1])
    return render_template("dashboard.html", d=data, max_rev=max_rev, nav="dashboard")
