"""Parser for commands.conf.

The table is read rather than hard-coded so that adding a subcommand is a
one-line edit that the help text, the dispatcher and the shell completion all
pick up at once. A hard-coded case list drifts from the help text.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

NOT_SUPPORTED = "-"


@dataclass(frozen=True)
class Command:
    name: str
    posix: str
    windows: str
    summary: str

    def impl_for(self, is_windows: bool) -> str:
        return self.windows if is_windows else self.posix

    def supported_on(self, is_windows: bool) -> bool:
        return self.impl_for(is_windows) != NOT_SUPPORTED


def conf_path() -> Path:
    return Path(__file__).resolve().parent / "commands.conf"


def parse(text: str) -> tuple[Command, ...]:
    commands = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            # A malformed row is skipped rather than fatal: a broken table must
            # not take out `fath doctor`, which is the command you would reach
            # for to find out why.
            continue
        commands.append(Command(parts[0], parts[1], parts[2], parts[3]))
    return tuple(commands)


@functools.lru_cache(maxsize=1)
def commands() -> tuple[Command, ...]:
    try:
        return parse(conf_path().read_text(encoding="utf-8"))
    except OSError:
        return ()


def get(name: str) -> Command | None:
    for command in commands():
        if command.name == name:
            return command
    return None


def names() -> tuple[str, ...]:
    return tuple(command.name for command in commands())
