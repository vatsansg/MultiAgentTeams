"""Extracts a run's downloaded site.zip and boots its local dev server.

Uses the machine-readable site/run.json manifest the Developer agent is
now required to produce (see DEVELOPER_SPECIALIST_SYSTEM in
agentprompts/prompts.py: install_cmd, start_cmd, url) instead of
guessing install/start commands per tech stack - the Developer can build
in Node, Python, .NET, or anything else in its skill list, and this stays
generic by just running whatever commands it wrote down and polling the
URL it wrote down.

Called from run_manager.py's background worker thread right after a
successful run whose team includes a Developer, so it never blocks the
Flask request thread. A site.zip built before this manifest requirement
existed will fail cleanly with a message saying to re-run the project.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

INSTALL_TIMEOUT = 300  # seconds allowed for the install command
READY_TIMEOUT = 120    # seconds allowed for the dev server to start responding
POLL_INTERVAL = 1.5

# Known local-machine-setup failure signatures -> a short, actionable fix,
# shown up front in the dashboard's "Local site" failure detail (see
# _diagnose below) instead of leaving the user to read a raw npm/node-gyp
# log to figure out what to do. Checked in order, first match wins - most
# specific first. Purely heuristic: no match just means no suggestion is
# shown, not that nothing is wrong.
_KNOWN_FIXES = [
    (re.compile(r"could not find any visual studio installation", re.I),
     "A native npm package needs to compile, but no C++ build toolchain is "
     "installed. Install the \"Desktop development with C++\" workload for "
     "Visual Studio Build Tools (https://visualstudio.microsoft.com/downloads/, "
     "under \"Tools for Visual Studio\"), then click Retry - no need to run "
     "the project again."),
    (re.compile(r"gyp err|node-gyp", re.I),
     "A native npm package failed to compile (node-gyp). Usually a missing "
     "C++ compiler toolchain (install Visual Studio Build Tools' \"Desktop "
     "development with C++\" workload) or a Node version too new to have a "
     "prebuilt binary yet (an LTS Node version - 20.x/22.x - is more likely "
     "to just work). Fix locally, then Retry."),
    (re.compile(r"'npm' is not recognized|npm: command not found|enoent.*npm", re.I),
     "npm isn't installed or not on PATH. Install Node.js (it bundles npm) "
     "and make sure it's on your system PATH, then Retry."),
    (re.compile(r"could not find python|python.*not found|gyp.*python", re.I),
     "node-gyp needs Python to build native modules. Install Python 3, "
     "make sure it's on PATH, then Retry."),
    (re.compile(r"eaddrinuse", re.I),
     "Something else on this machine is already using that port. Stop "
     "whatever's bound to it, then Retry."),
    (re.compile(r"'dotnet' is not recognized|dotnet: command not found", re.I),
     "This site needs the .NET SDK, which isn't installed or not on PATH. "
     "Install it from https://dotnet.microsoft.com/download, then Retry."),
    (re.compile(r"'python' is not recognized|python: command not found", re.I),
     "This site needs Python, which isn't installed or not on PATH. Install "
     "it, make sure it's on PATH, then Retry."),
    (re.compile(r"eacces|permission denied", re.I),
     "A permissions error while installing or starting. Check that nothing "
     "else has files in this folder locked/open, then Retry."),
]


def _diagnose(text: str) -> Optional[str]:
    """Matches recent log output against _KNOWN_FIXES. Returns the first
    matching suggestion, or None if nothing recognizable was found."""
    if not text:
        return None
    for pattern, suggestion in _KNOWN_FIXES:
        if pattern.search(text):
            return suggestion
    return None


def _format_error(detail: str, log_path: Path = None) -> str:
    """Builds the string stored in runs.dev_server_error. When a log file
    is given and something recognizable is in it, prefixes a plain-language
    suggested fix (shown prominently in the dashboard) ahead of the raw
    detail (shown collapsed) - separated by a marker line the dashboard
    template/JS split on, never shown to the user directly."""
    log_text = ""
    if log_path is not None and log_path.exists():
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
    suggestion = _diagnose(log_text) or _diagnose(detail)
    if suggestion:
        return f"{suggestion}\n---\n{detail}"
    return detail

# project_id -> {"process": Popen, "url": str, "log_file": file handle}
# In-memory only, same tradeoff run_manager.py already makes for
# _RUN_LOGS/_CANCEL_EVENTS - fine for a single-user local console. A
# process started here outlives an agent_console restart (it's not a
# child the OS kills with the parent on Windows), so restarting
# agent_console loses track of it; stop it via the dashboard's "Stop"
# button, or manually, before restarting if that matters to you.
_RUNNING: dict[int, dict] = {}
_LOCK = threading.Lock()


class LocalRunError(Exception):
    """Raised for anything that prevents the site from being started -
    caught by run_manager.py and recorded as dev_server_status='failed'."""


def stop_dev_server(project_id: int):
    """Terminates the previously started dev server for this project, if
    any. Called before starting a new one (a re-run replaces the running
    site the same way it already replaces the downloaded files on disk)
    and from the project-delete route so deleting a project doesn't leave
    an orphaned server behind."""
    with _LOCK:
        info = _RUNNING.pop(project_id, None)
    if not info:
        return
    process = info["process"]
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    info["log_file"].close()


def extract_site(site_zip: Path, project_dir: Path) -> Path:
    """Unzips site.zip into project_dir, replacing any previously
    extracted site/ folder (same overwrite-in-place behavior as the zip
    download itself)."""
    site_dir = project_dir / "site"
    if site_dir.exists():
        shutil.rmtree(site_dir)
    with zipfile.ZipFile(site_zip) as zf:
        zf.extractall(project_dir)
    if not site_dir.is_dir():
        raise LocalRunError(
            f"site.zip did not contain a top-level 'site/' folder (expected {site_dir})."
        )
    return site_dir


def _load_manifest(site_dir: Path) -> dict:
    manifest_path = site_dir / "run.json"
    if not manifest_path.exists():
        raise LocalRunError(
            "site/run.json is missing, so the site can't be started automatically. "
            "The Developer agent is required to write this manifest (install_cmd, "
            "start_cmd, url) - this site.zip may have been built before that "
            "requirement was added. Run the project again to regenerate it."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalRunError(f"site/run.json is not valid JSON: {exc}") from exc
    if not manifest.get("start_cmd") or not manifest.get("url"):
        raise LocalRunError("site/run.json must include at least 'start_cmd' and 'url'.")
    return manifest


def _probe_ready(url: str, deadline: float) -> bool:
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except urllib.error.HTTPError:
            return True  # the server responded, even with a non-2xx status - it's up
        except Exception:
            time.sleep(POLL_INTERVAL)
    return False


def start_dev_server(project_id: int, site_dir: Path,
                      on_event: Optional[Callable[[dict], None]] = None) -> dict:
    """Runs run.json's install command (blocking), launches its start
    command as a long-lived background process, then polls its url until
    something answers. Returns {"status": "ready"|"failed", "url", "error"}.
    """
    def emit(kind: str, **payload):
        if on_event:
            on_event({"kind": kind, **payload})

    stop_dev_server(project_id)

    try:
        manifest = _load_manifest(site_dir)
    except LocalRunError as exc:
        emit("dev_server_failed", error=str(exc))
        return {"status": "failed", "url": None, "error": str(exc)}

    log_path = site_dir / ".agent-console-devserver.log"
    log_file = open(log_path, "w", encoding="utf-8")

    install_cmd = manifest.get("install_cmd")
    if install_cmd:
        emit("dev_server_status", message=f"Installing dependencies: {install_cmd}")
        try:
            result = subprocess.run(
                install_cmd, cwd=str(site_dir), shell=True,
                stdout=log_file, stderr=subprocess.STDOUT, timeout=INSTALL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            log_file.close()
            error = _format_error(
                f"Install command timed out after {INSTALL_TIMEOUT}s: {install_cmd}. See {log_path}",
                log_path,
            )
            emit("dev_server_failed", error=error)
            return {"status": "failed", "url": None, "error": error}
        if result.returncode != 0:
            log_file.close()
            error = _format_error(
                f"Install command failed (exit {result.returncode}): {install_cmd}. See {log_path}",
                log_path,
            )
            emit("dev_server_failed", error=error)
            return {"status": "failed", "url": None, "error": error}

    emit("dev_server_status", message=f"Starting dev server: {manifest['start_cmd']}")
    process = subprocess.Popen(
        manifest["start_cmd"], cwd=str(site_dir), shell=True,
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    with _LOCK:
        _RUNNING[project_id] = {"process": process, "url": manifest["url"], "log_file": log_file}

    ready = _probe_ready(manifest["url"], time.time() + READY_TIMEOUT)

    if not ready:
        exited = process.poll()
        detail = f"process exited with code {exited}" if exited is not None else "process still running - check its log"
        error = _format_error(
            f"Dev server did not respond at {manifest['url']} within {READY_TIMEOUT}s ({detail}). See {log_path}",
            log_path,
        )
        emit("dev_server_failed", error=error)
        return {"status": "failed", "url": manifest["url"], "error": error}

    emit("dev_server_ready", url=manifest["url"])
    return {"status": "ready", "url": manifest["url"], "error": None}
