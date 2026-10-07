"""The dispatcher. Renders all help, so the shims cannot drift from the code.

Help text is built from commands.conf here rather than duplicated in the
PowerShell shim, which is what keeps `fath --help` identical on every platform by
construction instead of by discipline.

Exit codes have meaning to the shim:
    0  fine
    1  something failed
    2  the command line was wrong
"""

from __future__ import annotations

import sys
from typing import Any

from fath import __version__, logging_setup, paths, registry

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def render_help() -> str:
    is_windows = paths.is_windows()
    lines = [
        "fath - pull Fathom meetings onto disk, one folder each.",
        "",
        "usage: fath <command> [options]",
        "",
        "commands:",
    ]
    width = max((len(c.name) for c in registry.commands()), default=8)
    for command in registry.commands():
        if command.supported_on(is_windows):
            lines.append(f"  {command.name.ljust(width)}  {command.summary}")
        else:
            lines.append(f"  {command.name.ljust(width)}  (not available on this platform)")
    lines += [
        "",
        "global options:",
        "  --home <path>   use a different settings root for this run",
        "  --debug         log every rate-limit header and API call",
        "  -h, --help      this help, or `fath <command> --help`",
        "  -V, --version   print the version",
        "",
        f"settings root: {paths.resolution()[0]}  (from {paths.resolution()[1]})",
    ]
    return "\n".join(lines)


def _split_global_flags(argv: list[str]) -> tuple[list[str], str]:
    """Pull --home and --debug out before the subcommand sees them."""
    rest: list[str] = []
    level = "WARNING"
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--home" and index + 1 < len(argv):
            paths.set_override(argv[index + 1])
            index += 2
            continue
        if item.startswith("--home="):
            paths.set_override(item.split("=", 1)[1])
            index += 1
            continue
        if item == "--debug":
            level = "DEBUG"
            index += 1
            continue
        rest.append(item)
        index += 1
    return rest, level


def main(argv: list[str] | None = None) -> int:
    args, level = _split_global_flags(list(sys.argv[1:] if argv is None else argv))
    # --quiet belongs to `sync`, but logging is set up before the subcommand is
    # imported, so it has to be noticed here too.
    logging_setup.configure(level, quiet="--quiet" in args)

    if not args or args[0] in ("-h", "--help", "help"):
        print(render_help())
        return EXIT_OK
    if args[0] in ("-V", "--version"):
        print(f"fath {__version__}")
        return EXIT_OK

    name, rest = args[0], args[1:]
    command = registry.get(name)
    if command is None:
        print(f"fath: unknown command '{name}'", file=sys.stderr)
        print(f"try one of: {', '.join(registry.names())}", file=sys.stderr)
        return EXIT_USAGE
    if not command.supported_on(paths.is_windows()):
        print(f"fath {name}: not available on this platform", file=sys.stderr)
        return EXIT_USAGE

    try:
        module: Any = __import__(f"fath.commands.{name}", fromlist=["main"])
    except ImportError as exc:
        print(f"fath {name}: not implemented yet ({exc})", file=sys.stderr)
        return EXIT_ERROR

    try:
        return int(module.main(rest))
    except paths.PathsError as exc:
        print(f"fath {name}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        # A backfill can be minutes long and interrupting it is expected; the
        # database is committed per meeting, so nothing is lost.
        print("\ninterrupted", file=sys.stderr)
        return EXIT_ERROR
