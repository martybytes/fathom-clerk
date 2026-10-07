"""`fath status` -- what has been synced, and when."""

from __future__ import annotations

from fath import config, db, keystore, paths

HELP = """usage: fath status

Show the settings root, the output folder, the API key state and the last run.
"""


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(HELP)
        return 0

    conf = config.shared()
    database = db.shared()
    root, source = paths.resolution()
    key = keystore.describe()

    out_root = conf.output_root()
    print(f"settings root : {root}  (from {source})")
    print(f"output folder : {out_root}" + ("" if out_root.is_dir() else "  (not created yet)"))
    print(
        "api key       : "
        + (f"set, from {key['source']} (...{key['tail']})" if key["set"] else "NOT SET")
    )
    print(f"database      : {database.path}")
    print(f"meetings      : {database.count_meetings()}")
    print(f"search index  : {'FTS5' if database.fts_available else 'LIKE fallback'}")

    last = database.last_run()
    if last is None:
        print("last run      : never")
    else:
        print(
            f"last run      : #{last['id']} {last['status']} at {last['started_at']}"
            f" -- {last['written']} written, {last['skipped']} unchanged,"
            f" {last['requests_made']} requests"
        )
    active = database.active_run()
    if active is not None:
        print(f"in progress   : run #{active['id']} started {active['started_at']}")
    return 0
