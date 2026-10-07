"""`fath schedule` -- install, remove or inspect the Scheduled Task.

A thin wrapper over scripts/install-scheduled-task.ps1 so the CLI, the terminal
dashboard and the web app all drive one implementation. The PowerShell is the
implementation because Register-ScheduledTask has no usable Python equivalent
that does not mean hand-writing task XML.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from fath import paths

HELP = """usage: fath schedule [--install | --remove | --status | --run-now]

options:
  --status            what the task is doing (the default)
  --install           create or replace the task
  --remove            delete the task
  --run-now           trigger it immediately, without waiting
  --every <hours>     repeat interval for --install (default 4)
  --at <HH:MM>        first run of the day for --install (default 07:00)
  -h, --help          this help

The task runs `fath sync --quiet` with no console window, catches up after the
machine has been asleep, and never runs two syncs at once. It needs no
elevation and stores no password.
"""

SCRIPT = "install-scheduled-task.ps1"


def _pwsh() -> str | None:
    # pwsh first: Windows PowerShell 5.1 works for these cmdlets, but the repo
    # targets 7+ everywhere else and mixing the two is how quoting bugs appear.
    for candidate in ("pwsh", "powershell"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(HELP)
        return 0

    if not paths.is_windows():
        print("fath schedule: Scheduled Tasks are Windows-only", file=sys.stderr)
        print("  on POSIX, use cron or a systemd timer to run: fath sync --quiet", file=sys.stderr)
        return 2

    script = paths.repo_root() / "scripts" / SCRIPT
    if not script.is_file():
        print(f"fath schedule: cannot find {script}", file=sys.stderr)
        return 1

    shell = _pwsh()
    if shell is None:
        print("fath schedule: PowerShell not found on PATH", file=sys.stderr)
        return 1

    forwarded: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--install":
            pass  # the script installs when given no switch
        elif item == "--remove":
            forwarded.append("-Uninstall")
        elif item == "--status":
            forwarded.append("-Status")
        elif item == "--run-now":
            forwarded.append("-RunNow")
        elif item == "--every" and index + 1 < len(argv):
            forwarded += ["-IntervalHours", argv[index + 1]]
            index += 1
        elif item == "--at" and index + 1 < len(argv):
            forwarded += ["-StartTime", argv[index + 1]]
            index += 1
        else:
            print(f"fath schedule: unknown option '{item}' (try -h)", file=sys.stderr)
            return 2
        index += 1

    if not argv:
        forwarded.append("-Status")

    command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *forwarded]
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"fath schedule: could not run PowerShell: {exc}", file=sys.stderr)
        return 1
