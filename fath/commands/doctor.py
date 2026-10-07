"""`fath doctor` -- diagnose problems, and say exactly what to do about them.

A check produces a Result, not a printed line. That separation is what lets
`--json` be a read model rather than a second rendering of the same prose, and it
is why every finding can be asserted by its stable id instead of by matching
text.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from fath import config, db, keystore, naming, paths, registry

OK = "ok"
FAIL = "fail"
NOTE = "note"

HELP = """usage: fath doctor [--json]

Check the settings root, the API key, the output folder, the database and the
naming rules. Reports what is wrong and the exact next action.
"""


@dataclass(frozen=True)
class Result:
    check: str  # stable id, safe to assert on; never the prose
    status: str
    message: str
    hint: str = ""

    def as_json(self) -> dict[str, str]:
        return {
            "check": self.check,
            "status": self.status,
            "message": self.message,
            "hint": self.hint,
        }


def _check_home() -> list[Result]:
    try:
        root, source = paths.resolution()
    except Exception as exc:
        return [Result("home.resolve", FAIL, f"could not work out the settings root: {exc}")]

    results = [Result("home.source", OK, f"settings root {root} (from {source})")]
    try:
        paths.fath_home()
    except paths.PathsError as exc:
        results.append(
            Result(
                "home.writable",
                FAIL,
                str(exc),
                "choose another with: fath init --home <path>",
            )
        )
        return results

    pointer = paths.pointer_path()
    if pointer.is_file() and source.startswith("pointer"):
        results.append(Result("home.pointer", NOTE, f"pointer file in use: {pointer}"))
    return results


def _check_key() -> list[Result]:
    described = keystore.describe()
    if not described["set"]:
        return [
            Result(
                "key.present",
                FAIL,
                "no Fathom API key",
                "set one with `fath key --set`, or in the web app. "
                "Generate it at https://fathom.video/customize#api-access-header",
            )
        ]
    return [
        Result(
            "key.present", OK, f"API key set, from {described['source']} (...{described['tail']})"
        )
    ]


def _check_output(conf: config.Config) -> list[Result]:
    root = conf.output_root()
    results = []
    if root.is_dir():
        folders = sum(1 for p in root.iterdir() if p.is_dir())
        results.append(Result("output.exists", OK, f"output folder {root} ({folders} folders)"))
    else:
        results.append(
            Result(
                "output.exists",
                NOTE,
                f"output folder {root} does not exist yet",
                "it is created on the first sync",
            )
        )

    rules = naming.NamingRules.from_config(conf)
    cap = naming.max_name_len(root, rules)
    if cap <= rules.min_name_len:
        results.append(
            Result(
                "output.depth",
                FAIL,
                f"the output folder is so deep that names are capped at {cap} characters",
                "choose a shallower output folder: fath config outputRoot C:\\Fathom",
            )
        )
    elif cap < rules.max_name_len:
        results.append(
            Result(
                "output.depth",
                NOTE,
                f"folder names are capped at {cap} characters by the path length limit",
                "a shallower output folder would allow longer names",
            )
        )
    return results


def _check_existing_folders(conf: config.Config) -> list[Result]:
    """Folders already on disk that are too long for the current output root.

    The cap is derived from the output root, so moving meetings to a deeper
    folder can put names that were legal where they were written over the limit
    where they now are. Moving exports into a deeper directory can put otherwise valid
    files past MAX_PATH.

    fath never writes such a path -- writers.write_meeting refuses -- so this
    only ever comes from folders that arrived some other way. It still matters:
    those files cannot be opened by every tool, and a re-sync will write the
    meeting under a new shorter name and leave the old folder behind.
    """
    root = conf.output_root()
    if not root.is_dir():
        return []

    rules = naming.NamingRules.from_config(conf)
    cap = naming.max_name_len(root, rules)
    long_names = 0
    long_paths = 0
    worst = ""
    try:
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            if len(folder.name) > cap:
                long_names += 1
                if len(folder.name) > len(worst):
                    worst = folder.name
            for child in folder.iterdir():
                if len(str(child)) >= naming.WINDOWS_MAX_PATH:
                    long_paths += 1
    except OSError as exc:
        return [Result("output.scan", NOTE, f"could not scan the output folder: {exc}")]

    if not long_names and not long_paths:
        return [Result("output.lengths", OK, "every folder fits this output path")]

    detail = f"{long_names} folder name(s) exceed the {cap}-character limit for this path"
    if long_paths:
        detail += f", and {long_paths} file(s) are at or over {naming.WINDOWS_MAX_PATH} characters"
    return [
        Result(
            "output.lengths",
            FAIL if long_paths else NOTE,
            detail,
            "these were written under a shallower output folder. Either move the "
            "output root somewhere shorter, or run `fath sync --overwrite` to "
            "rewrite them at the current limit (the old folders are left behind "
            f"for you to delete). Longest: {worst}",
        )
    ]


def _check_database() -> list[Result]:
    try:
        database = db.shared()
    except sqlite3.Error as exc:
        return [Result("db.open", FAIL, f"could not open the database: {exc}")]

    results = [
        Result("db.open", OK, f"database {database.path} ({database.count_meetings()} meetings)")
    ]
    if not database.fts_available:
        results.append(
            Result(
                "db.fts",
                NOTE,
                "this Python has no FTS5, so search falls back to a slower LIKE scan",
                "everything else works normally",
            )
        )
    stale = database.active_run()
    if stale is not None:
        results.append(
            Result(
                "db.stale_run",
                NOTE,
                f"run #{stale['id']} is still marked running",
                "if no sync is actually going, the next one will clear it",
            )
        )
    return results


def _check_commands() -> list[Result]:
    missing = []
    for command in registry.commands():
        if not command.supported_on(paths.is_windows()):
            continue
        try:
            __import__(f"fath.commands.{command.name}", fromlist=["main"])
        except ImportError:
            missing.append(command.name)
    if missing:
        return [
            Result(
                "commands.implemented",
                NOTE,
                f"listed but not built yet: {', '.join(missing)}",
                "they are in the plan; everything else works",
            )
        ]
    return [Result("commands.implemented", OK, f"{len(registry.names())} commands available")]


def run_checks() -> list[Result]:
    results = _check_home()
    if any(r.status == FAIL and r.check.startswith("home.") for r in results):
        return results  # nothing below can be trusted without a settings root
    conf = config.shared()
    return [
        *results,
        *_check_key(),
        *_check_output(conf),
        *_check_existing_folders(conf),
        *_check_database(),
        *_check_commands(),
    ]


MARK = {OK: "ok  ", FAIL: "FAIL", NOTE: "note"}


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(HELP)
        return 0

    results = run_checks()

    if argv and argv[0] == "--json":
        payload: dict[str, Any] = {
            "ok": not any(r.status == FAIL for r in results),
            "checks": [r.as_json() for r in results],
        }
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1

    for result in results:
        print(f"{MARK[result.status]}  {result.message}")
        if result.hint:
            print(f"      -> {result.hint}")

    failures = sum(1 for r in results if r.status == FAIL)
    print()
    print("all good" if not failures else f"{failures} problem(s) found")
    return 1 if failures else 0
