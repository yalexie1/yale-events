"""Runs `yev scrape` then `yev sources check --notify` every few hours via a per-user launchd agent (macOS)."""

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

LABEL = "local.yale-events.scrape"
PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def build_plist(project_dir: Path, every_hours: float, python: str = sys.executable) -> dict:
    project_dir = project_dir.resolve()
    yev = f"{shlex.quote(python)} -m yale_events.cli"
    env = {"PYTHONPATH": str(project_dir / "src"), "PATH": "/usr/bin:/bin"}
    for var in ("YEV_CONTACT", "YEV_DB_URL"):
        if os.environ.get(var):
            env[var] = os.environ[var]
    log = project_dir / "data/logs/scrape.log"
    return {
        "Label": LABEL,
        # The check runs even if some sources failed, and posts a notification if anything needs attention.
        "ProgramArguments": ["/bin/sh", "-c", f"date; {yev} scrape; {yev} sources check --notify"],
        "WorkingDirectory": str(project_dir),
        "EnvironmentVariables": env,
        "StartInterval": int(every_hours * 3600),
        "RunAtLoad": True,
        # Don't hog the machine: launchd deprioritizes CPU and I/O for background jobs.
        "ProcessType": "Background",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def install(project_dir: Path, every_hours: float) -> Path:
    (project_dir / "data/logs").mkdir(parents=True, exist_ok=True)
    uninstall()  # reloading picks up a changed interval or path
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(build_plist(project_dir, every_hours)))
    subprocess.run(["launchctl", "bootstrap", _domain(), str(PLIST_PATH)], check=True)
    return PLIST_PATH


def uninstall() -> bool:
    if not PLIST_PATH.exists():
        return False
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    PLIST_PATH.unlink()
    return True


def status() -> str | None:
    """launchd's view of the job, or None if it isn't loaded."""
    r = subprocess.run(["launchctl", "print", f"{_domain()}/{LABEL}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def notify(title: str, message: str) -> None:
    # Pass text as argv so quotes in messages can't break the AppleScript.
    script = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run"]
    subprocess.run(["osascript", *script, title, message], capture_output=True)


def _domain() -> str:
    return f"gui/{os.getuid()}"
