"""APScheduler integration: reads each project's schedule from SQLite and
triggers a pipeline run automatically, the same way the dashboard's
"Run now" button does manually.

Two schedule kinds are supported, matching the dashboard form:
- interval_minutes: run every N minutes
- cron_expression: standard 5-field cron (e.g. "0 6 * * *" = daily at 06:00)

Only one job per project exists in the scheduler at a time; changing a
project's schedule replaces its job.
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import db
import run_manager

_scheduler = BackgroundScheduler(daemon=True)
_STARTED = False


def _job_id(project_id: int) -> str:
    return f"project-{project_id}"


def _run_scheduled(project_id: int):
    # A project may have been deleted since the job was scheduled.
    if db.get_project(project_id) is None:
        remove_job(project_id)
        return
    run_manager.start_run(project_id, trigger_type="scheduled")


def add_or_update_job(project_id: int, cron_expression: str, interval_minutes):
    remove_job(project_id)
    if cron_expression:
        trigger = CronTrigger.from_crontab(cron_expression)
    elif interval_minutes:
        trigger = IntervalTrigger(minutes=int(interval_minutes))
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


def sync_jobs_from_db():
    """Rebuild every scheduled job from the projects table. Call once at
    startup so schedules survive an app restart."""
    for job in list(_scheduler.get_jobs()):
        job.remove()
    for project in db.list_projects():
        if project["schedule_enabled"]:
            add_or_update_job(project["id"], project["cron_expression"], project["interval_minutes"])


def start():
    global _STARTED
    if _STARTED:
        return
    sync_jobs_from_db()
    _scheduler.start()
    _STARTED = True


def list_jobs():
    return _scheduler.get_jobs()
