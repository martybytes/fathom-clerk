"""`fath init` -- first-run setup, and where the settings root is chosen.

The root is configurable but cannot be stored in the config it points at, so a
non-default choice is recorded in a one-line pointer file at the fixed location.
Choosing the default writes nothing: an unconfigured install leaves no trace.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from fath import config, db, keystore, paths

HELP = """usage: fath init [options]

options:
  --home <path>          keep settings and the database here
                         (default: %LOCALAPPDATA%\\fathom-helper)
  --output-root <path>   write meeting folders here
                         (default: Documents\\Fathom)
  --import <path>        adopt meeting folders already at this path
  --force                re-run against an existing root
  --yes                  take the defaults, ask nothing
  -h, --help             this help

Re-pointing to a new settings root does not move what is already there. It
prints both paths and the copy command, then stops.
"""


def _ask(prompt: str, default: str, assume_yes: bool) -> str:
    if assume_yes or not sys.stdin.isatty():
        return default
    reply = input(f"{prompt}\n  [{default}]: ").strip()
    return reply or default


def _write_pointer(target: Path) -> None:
    pointer = paths.pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(target.resolve()), encoding="utf-8")


def main(argv: list[str]) -> int:
    home_arg = ""
    output_arg = ""
    import_arg = ""
    force = False
    assume_yes = False

    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("-h", "--help"):
            print(HELP)
            return 0
        if item == "--home" and index + 1 < len(argv):
            home_arg = argv[index + 1]
            index += 1
        elif item == "--output-root" and index + 1 < len(argv):
            output_arg = argv[index + 1]
            index += 1
        elif item == "--import" and index + 1 < len(argv):
            import_arg = argv[index + 1]
            index += 1
        elif item == "--force":
            force = True
        elif item in ("--yes", "-y"):
            assume_yes = True
        else:
            print(f"fath init: unknown option '{item}' (try: fath init -h)", file=sys.stderr)
            return 2
        index += 1

    # -- the settings root -------------------------------------------------- #
    existing_root, existing_source = paths.resolution()
    already_set_up = (existing_root / "config.json").is_file()

    if already_set_up and not force and not home_arg:
        print(f"already set up at {existing_root} (from {existing_source})")
        print("  re-run with --force to change anything")
        return 0

    default_home = str(paths.default_base())
    chosen_home = home_arg or (
        default_home
        if already_set_up
        else _ask("Where should settings live?", default_home, assume_yes)
    )
    target = Path(chosen_home).expanduser()

    if already_set_up and target.resolve() != existing_root.resolve():
        # Silently orphaning a synced tree and a stored key is worse than making
        # the move an explicit, visible step.
        print("a settings root already exists somewhere else:")
        print(f"  now : {existing_root}")
        print(f"  new : {target}")
        print("\nnothing has been moved. To carry it across, run:")
        print(f'  robocopy "{existing_root}" "{target}" /E')
        print(f'then: fath init --home "{target}" --force')
        return 1

    paths.set_override(target)
    config.reset_shared()
    try:
        root = paths.fath_home()
    except paths.PathsError as exc:
        print(f"fath init: {exc}", file=sys.stderr)
        return 1

    if target.resolve() == Path(default_home).resolve():
        # The default needs no pointer; leave nothing behind.
        pointer = paths.pointer_path()
        if pointer.is_file() and force:
            pointer.unlink()
    else:
        _write_pointer(target)

    print(f"settings root : {root}")

    # -- the output root ---------------------------------------------------- #
    conf = config.shared()
    default_output = str(conf.output_root())
    chosen_output = output_arg or _ask(
        "Where should meeting folders go?", default_output, assume_yes
    )
    if chosen_output != default_output or output_arg:
        conf.set("outputRoot", str(Path(chosen_output).expanduser()))
    out_root = conf.output_root()
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"output folder : {out_root}")

    # -- the API key -------------------------------------------------------- #
    described = keystore.describe()
    if described["set"]:
        print(f"api key       : already set, from {described['source']} (...{described['tail']})")
    elif assume_yes or not sys.stdin.isatty():
        print("api key       : not set (run `fath key --set`)")
    else:
        print("\nGenerate a key at https://fathom.video/customize#api-access-header")
        value = getpass.getpass("Fathom API key (blank to skip): ").strip()
        if value and keystore.looks_like_api_key(value):
            keystore.set_api_key(value)
            print(f"api key       : stored in {paths.env_file()}")
        elif value:
            print("that does not look like an API key; skipped", file=sys.stderr)

    # -- adopt what is already on disk -------------------------------------- #
    source = Path(import_arg).expanduser() if import_arg else out_root
    if source.is_dir():
        from fath.sync import SyncEngine

        engine = SyncEngine(conf, db.shared())
        adopted = sum(1 for _ in engine.reimport(source))
        if adopted:
            print(f"adopted       : {adopted} folder(s) from {source}, with no API requests")

    print("\nready. Next: fath sync   (or fath web for the dashboard)")
    return 0
