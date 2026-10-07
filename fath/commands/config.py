"""`fath config` -- view and change saved settings.

Renders from fath/schema.py, the same declaration the Textual and web settings
screens use, so a setting added once appears in all three with its help text and
its validation intact.
"""

from __future__ import annotations

import json
import sys

from fath import config as settings
from fath import schema

HELP = """usage: fath config [options]
       fath config <key>
       fath config <key> <value>

With no arguments, print every setting grouped, with the value that is in force
and which layer it came from.

options:
  --json         machine-readable, for scripts and the UIs
  --advanced     include the settings normally hidden behind a disclosure
  --defaults     show the built-in default beside each value
  --unset <key>  remove the saved value so the default applies again
  -h, --help     this help
"""

SOURCE_MARK = {"default": " ", "config.json": "*", "local.json": "L"}


def _format(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _show_all(advanced: bool, show_defaults: bool) -> int:
    rows = schema.snapshot()
    width = max(len(str(r["key"])) for r in rows)

    for group in schema.GROUPS:
        in_group = [
            r
            for r in rows
            if r["group"] == group and (advanced or schema.ADVANCED not in list(r["flags"]))
        ]
        if not in_group:
            continue
        print(f"\n{group}")
        for row in in_group:
            mark = SOURCE_MARK.get(str(row["source"]), "?")
            line = f"  {mark} {str(row['key']).ljust(width)}  {_format(row['value'])}"
            if show_defaults and row["value"] != row["default"]:
                line += f"   (default: {_format(row['default'])})"
            print(line)

    print("\n  * set in config.json    L set in local.json    (blank) built-in default")
    if not advanced:
        print("  some settings are hidden; use --advanced to see them")
    return 0


def _show_one(key: str) -> int:
    setting = schema.get(key)
    if setting is None:
        print(f"fath config: no setting called '{key}'", file=sys.stderr)
        return 2
    described = schema.describe(key)
    print(f"{key}")
    print(f"  value    : {_format(described['value'])}")
    print(f"  default  : {_format(described['default'])}")
    print(f"  from     : {described['source']}")
    print(f"  type     : {described['kind']}")
    if setting.options:
        print(f"  options  : {', '.join(setting.options)}")
    if setting.note:
        print(f"  {setting.note}")
    return 0


def _parse_value(raw: str) -> object:
    """Accept JSON for lists and maps, plain text for everything else."""
    stripped = raw.strip()
    if stripped[:1] in "[{":
        try:
            return json.loads(stripped)
        except ValueError:
            return raw
    return raw


def main(argv: list[str]) -> int:
    advanced = False
    show_defaults = False
    args: list[str] = []

    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("-h", "--help"):
            print(HELP)
            return 0
        if item == "--json":
            print(json.dumps(schema.snapshot(), indent=2, ensure_ascii=False))
            return 0
        if item == "--advanced":
            advanced = True
        elif item == "--defaults":
            show_defaults = True
        elif item == "--unset" and index + 1 < len(argv):
            key = argv[index + 1]
            if schema.get(key) is None:
                print(f"fath config: no setting called '{key}'", file=sys.stderr)
                return 2
            # Assign the built-in default rather than deleting the key: one
            # writer, one code path, and the file stays a complete document.
            settings.shared().set(key, schema.BY_KEY[key].default())
            print(f"{key} reset to its default")
            return 0
        elif item.startswith("-"):
            print(f"fath config: unknown option '{item}' (try: fath config -h)", file=sys.stderr)
            return 2
        else:
            args.append(item)
        index += 1

    if not args:
        return _show_all(advanced, show_defaults)
    if len(args) == 1:
        return _show_one(args[0])

    key, raw = args[0], " ".join(args[1:])
    setting = schema.get(key)
    if setting is None:
        print(f"fath config: no setting called '{key}'", file=sys.stderr)
        return 2

    value = _parse_value(raw)
    errors = schema.validate_many({key: value})
    if errors:
        print(f"fath config: {key} {errors[key]}", file=sys.stderr)
        return 2

    settings.shared().set(key, setting.coerce(value))
    described = schema.describe(key)
    print(f"{key} = {_format(described['value'])}")
    if schema.RESTART in setting.flags:
        print("  (takes effect on the next sync)")
    return 0
