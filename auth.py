"""Single-user login: one username/password from environment variables,
a Flask session cookie, and a `login_required` decorator for routes.

Deliberately not Flask-Login/a database - this console is meant for one
person to manage their own agents, per the "simple single-user login"
choice. Swap this module out for Flask-Login + a users table if that
changes later.
"""
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import config

bp = Blueprint("auth", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == config.CONSOLE_USERNAME and password == config.CONSOLE_PASSWORD:
            session["logged_in"] = True
            session["username"] = username
            next_url = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_url)
        flash("Incorrect username or password.", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
