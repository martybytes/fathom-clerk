"""`fath sync` -- fetch meetings into folders."""

from __future__ import annotations

import sys

from fath import config, sync

HELP = """usage: fath sync [options]

Fetch new meetings into folders under the output root.

options:
  --dry-run          show the folder names, write nothing
  --all              re-check every meeting, not just what is new
  --overwrite        rewrite folders even when the meeting is unchanged
  --no-reimport      skip the disk scan that adopts existing folders
  --since <date>     only meetings created after this (YYYY-MM-DD)
  --limit <n>        stop after n meetings
  --quiet            only print the summary (used by the scheduled task)
  -h, --help         this help

Fathom allows 10 API requests per minute. One request returns about 10 meetings
with their transcripts, so a full history costs roughly one request per ten
meetings and this paces itself to stay inside the limit rather than failing.
"""


def main(argv: list[str]) -> int:
    quiet = False
    kwargs: dict[str, object] = {}

    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("-h", "--help"):
            print(HELP)
            return 0
        elif item == "--dry-run":
            kwargs["dry_run"] = True
        elif item == "--overwrite":
            kwargs["overwrite"] = True
        elif item == "--all":
            kwargs["created_after"] = ""
        elif item == "--no-reimport":
            kwargs["reimport"] = False
        elif item == "--quiet":
            quiet = True
        elif item == "--since" and index + 1 < len(argv):
            kwargs["created_after"] = argv[index + 1]
            index += 1
        elif item == "--limit" and index + 1 < len(argv):
            try:
                kwargs["limit"] = int(argv[index + 1])
            except ValueError:
                print(
                    f"fath sync: --limit needs a number, got {argv[index + 1]!r}", file=sys.stderr
                )
                return 2
            index += 1
        else:
            print(f"fath sync: unknown option '{item}' (try: fath sync -h)", file=sys.stderr)
            return 2
        index += 1

    conf = config.shared()
    options = sync.SyncOptions.from_config(conf, **kwargs)

    failed = False
    for event in sync.run_to_database(options, conf):
        if event.kind == sync.ERROR:
            failed = True
            print(f"error: {event.message}", file=sys.stderr)
        elif event.kind == sync.MEETING and not quiet:
            print(f"  [{event.index}] {event.folder}")
        elif event.kind == sync.THROTTLED and not quiet:
            print(f"  ~ {event.message}")
        elif event.kind in (sync.STARTED, sync.FINISHED):
            if not quiet or event.kind == sync.FINISHED:
                print(event.message)
        elif event.kind == sync.IMPORTED and not quiet and event.folder == "":
            print(event.message)

    return 1 if failed else 0
