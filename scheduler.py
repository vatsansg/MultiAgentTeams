"""APScheduler integration: reads each project's schedule from SQLite and
triggers a pipeline run automatically, the same way the dashboard's
"Run now" button does manually.

Schedules are entered through a friendly picker (daily / weekly on a
weekday / monthly on a day-of-month / "every N minutes"), not raw cron
syntax. `parse_schedule_form()` turns the dashboard's structured form
fields into a schedule dict; `add_or_update_job()` turns that dict into an
APScheduler trigger. The dict shape (not a crontab string) is the schema
db.py's projects.schedule_* columns store, so both directions - form to
DB and DB to trigger - go through the same shape.

Only one job per project exists in the scheduler at a time; changing a
project's schedule replaces its job.
"""
from datetime import time as _time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import db
import run_manager

_scheduler = BackgroundScheduler(daemon=True)
_STARTED = False

# CronTrigger takes weekday names directly, not numbers - sidesteps any
# 0=Sunday-vs-0=Monday numbering-convention ambiguity entirely. Index
# 0=Monday..6=Sunday matches the dashboard's weekday <select> values.
_WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WEEKDAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _job_id(project_id: int) -> str:
    return f"project-{project_id}"


def _run_scheduled(project_id: int):
    # A project may have been deleted since the job was scheduled.
    if db.get_project(project_id) is None:
        remove_job(project_id)
        return
    run_manager.start_run(project_id, trigger_type="scheduled")


def _format_time(hour: int, minute: int, second: int) -> str:
    return _time(hour, minute, second).strftime("%I:%M:%S %p").lstrip("0")


def parse_schedule_form(form) -> tuple:
    """Parses the dashboard's structured schedule fields into the dict
    shape db.create_project/update_schedule and add_or_update_job expect.

    Returns (schedule_dict_or_None, error_or_None). schedule is None with
    no error when every field was left blank (manual only, no schedule).
    """
    frequency = (form.get("schedule_frequency") or "").strip().lower()
    if not frequency:
        return None, None

    if frequency == "interval":
        raw = (form.get("interval_minutes") or "").strip()
        if not raw.isdigit() or int(raw) < 5:
            return None, "Enter an interval of at least 5 minutes."
        minutes = int(raw)
        return {
            "frequency": "interval", "interval_minutes": minutes,
            "summary": f"Every {minutes} minute{'s' if minutes != 1 else ''}",
        }, None

    if frequency not in ("daily", "weekly", "monthly"):
        return None, f"Unknown schedule frequency: {frequency!r}"

    use_12h = form.get("time_mode") == "12h"
    try:
        minute = int(form.get("schedule_minute") or 0)
        second = int(form.get("schedule_second") or 0)
        if use_12h:
            hour_12 = int(form.get("schedule_hour_12") or 12)
            period = (form.get("schedule_period") or "AM").upper()
            if not (1 <= hour_12 <= 12) or period not in ("AM", "PM"):
                raise ValueError
            hour = hour_12 % 12
            if period == "PM":
                hour += 12
        else:
            hour = int(form.get("schedule_hour") or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise ValueError
    except (TypeError, ValueError):
        return None, "Enter a valid time."

    schedule = {"frequency": frequency, "hour": hour, "minute": minute, "second": second}

    if frequency == "weekly":
        weekday = form.get("schedule_weekday")
        if not weekday or not weekday.isdigit() or not (0 <= int(weekday) <= 6):
            return None, "Pick a day of the week."
        schedule["weekday"] = int(weekday)
        schedule["summary"] = (
            f"Every {_WEEKDAY_LABELS[int(weekday)]} at {_format_time(hour, minute, second)}"
        )
    elif frequency == "monthly":
        month_day = form.get("schedule_month_day")
        if not month_day or not month_day.isdigit() or not (1 <= int(month_day) <= 31):
            return None, "Pick a day of the month (1-31)."
        schedule["month_day"] = int(month_day)
        schedule["summary"] = f"Day {month_day} of every month at {_format_time(hour, minute, second)}"
    else:
        schedule["summary"] = f"Every day at {_format_time(hour, minute, second)}"

    return schedule, None


def add_or_update_job(project_id: int, schedule: dict):
    remove_job(project_id)
    if not schedule:
        return
    frequency = schedule.get("frequency")
    if frequency == "interval":
        trigger = IntervalTrigger(minutes=int(schedule["interval_minutes"]))
    elif frequency == "daily":
        trigger = CronTrigger(
            hour=schedule["hour"], minute=schedule["minute"], second=schedule.get("second", 0),
        )
    elif frequency == "weekly":
        trigger = CronTrigger(
            day_of_week=_WEEKDAY_NAMES[schedule["weekday"]],
            hour=schedule["hour"], minute=schedule["minute"], second=schedule.get("second", 0),
        )
    elif frequency == "monthly":
        trigger = CronTrigger(
            day=schedule["month_day"],
            hour=schedule["hour"], minute=schedule["minute"], second=schedule.get("second", 0),
        )
    else:
        return
    _scheduler.add_job(
        _run_scheduled, trigger=trigger, args=[project_id], id=_job_id(project_id),
        replace_existing=True,
    )


def remove_job(project_id: int):
    job_id = _job_id(project_id)
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)


def _schedule_from_project_row(project):
    """Rebuilds the schedule dict add_or_update_job expects from a
    projects row's stored schedule_* columns - used at startup to restore
    every enabled schedule as an APScheduler job."""
    frequency = project["schedule_frequency"]
    if not frequency:
        return None
    if frequency == "interval":
        if not project["interval_minutes"]:
            return None
        return {"frequency": "interval", "interval_minutes": project["interval_minutes"]}
    schedule = {
        "frequency": frequency,
        "hour": project["schedule_hour"] or 0,
        "minute": project["schedule_minute"] or 0,
        "second": project["schedule_second"] or 0,
    }
    if frequency == "weekly":
        schedule["weekday"] = project["schedule_weekday"] or 0
    elif frequency == "monthly":
        schedule["month_day"] = project["schedule_month_day"] or 1
    return schedule


def sync_jobs_from_db():
    """Rebuild every scheduled job from the projects table. Call once at
    startup so schedules survive an app restart."""
    for job in list(_scheduler.get_jobs()):
        job.remove()
    for project in db.list_projects():
        if project["schedule_enabled"]:
            schedule = _schedule_from_project_row(project)
            if schedule:
                add_or_update_job(project["id"], schedule)


def start():
    global _STARTED
    if _STARTED:
        return
    sync_jobs_from_db()
    _scheduler.start()
    _STARTED = True


def list_jobs():
    return _scheduler.get_jobs()
