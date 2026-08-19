"""Agent Console - a small Flask app: login, a visual layout of the
Delivery Lead / Roger / Michael agent roster, on-demand run triggers, and
schedule management backed by APScheduler.

Run locally:
    python app.py
Or with the Flask CLI:
    flask --app app run
"""
from flask import Flask

import config
import db
import scheduler
from auth import bp as auth_bp, login_required
from dashboard import bp as dashboard_bp


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY

    db.init_db()
    # A run stuck 'running' means a previous process died or was stopped
    # mid-run and nothing ever called finish_run() for it - reconcile those
    # now so a restart doesn't leave the dashboard showing a permanently
    # stuck row (see db.fail_orphaned_running_runs's docstring).
    reconciled = db.fail_orphaned_running_runs()
    if reconciled:
        print(f"[agent_console] marked {reconciled} orphaned 'running' run(s) as failed on startup")

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    # Every dashboard route needs a login except the auth blueprint itself.
    for endpoint, view_func in list(app.view_functions.items()):
        if endpoint.startswith("dashboard."):
            app.view_functions[endpoint] = login_required(view_func)

    scheduler.start()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
