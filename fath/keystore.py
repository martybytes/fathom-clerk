"""The Fathom API key: read from the environment or .env, written to .env.

Read order is environment, then the repo .env, then nothing. The environment
wins so a one-off run can override without editing a file.

Writing is a **key splice, never a whole-file rewrite**. The .env also holds the
webhook secret and whatever comments were put there by hand, and rewriting the
document from a parsed dict silently discards all of it. Never whole-file-copy
a file its own owner also edits -- splice it.

Key names are normalised by upper-casing and folding "-" to "_", so
FATHOM-PERSONAL-API-KEY and FATHOM_PERSONAL_API_KEY are one entry. That matters
here: the dashed spelling cannot be a real environment variable, because
`export FATHOM-PERSONAL-API-KEY=x` is a shell syntax error. Folding lets the
file keep working whichever way it ends up written.

Nothing in this module returns the key to a UI. describe() reports only whether
one is set, where it came from, and the last four characters -- enough to tell
two keys apart in a screenshot without revealing either.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import stat
import tempfile
from pathlib import Path

from fath import paths

log = logging.getLogger(__name__)

# Most specific first. The webhook secret is listed so the writer can find it,
# not because this module ever reads it.
API_KEY_ALIASES = (
    "FATHOM_PERSONAL_API_KEY",
    "FATHOM_PERSON_API_KEY",
    "FATHOM_API_KEY",
    "FATHOM_KEY",
)
WEBHOOK_SECRET_ALIASES = (
    "FATHOM_PERSONAL_WEBHOOK_SECRET",
    "FATHOM_PERSON_WEBHOOK_SECRET",
    "FATHOM_WEBHOOK_SECRET",
)

# The spelling written when no existing line matches. Dashed to match the file
# the user already has, so a fresh write does not leave two spellings behind.
DEFAULT_KEY_NAME = "FATHOM-PERSONAL-API-KEY"


def normalise(name: str) -> str:
    return name.strip().upper().replace("-", "_")


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE pairs with normalised keys. Comments and blanks ignored."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        raw_key, _, raw_value = line.partition("=")
        key = normalise(raw_key)
        value = raw_value.strip().strip("'").strip('"')
        if key and value:
            values[key] = value
    return values


def load_env_file(path: Path | None = None) -> dict[str, str]:
    target = path or paths.env_file()
    try:
        return parse_env(target.read_text(encoding="utf-8"))
    except OSError:
        return {}


def _from_environment(aliases: tuple[str, ...]) -> tuple[str, str]:
    for name in aliases:
        value = os.environ.get(name, "").strip()
        if value:
            return value, f"${name}"
    return "", ""


def resolve(aliases: tuple[str, ...] = API_KEY_ALIASES) -> tuple[str, str]:
    """Return (value, source). Source is "$NAME", ".env (NAME)", or "".

    Never raises. An unreadable or missing .env is "no value", so a caller can
    prompt rather than crash.
    """
    value, source = _from_environment(aliases)
    if value:
        return value, source
    from_file = load_env_file()
    for name in aliases:
        if from_file.get(name):
            return from_file[name], f".env ({name})"
    return "", ""


def api_key() -> str:
    return resolve()[0]


def describe(aliases: tuple[str, ...] = API_KEY_ALIASES) -> dict[str, object]:
    """Safe-to-render status: never the value, only where it came from."""
    value, source = resolve(aliases)
    return {
        "set": bool(value),
        "source": source,
        "tail": value[-4:] if len(value) >= 8 else "",
        "envFile": str(paths.env_file()),
        "names": list(aliases),
    }


def _splice(text: str, target_names: tuple[str, ...], value: str, write_name: str) -> str:
    """Replace the first matching assignment in place, or append a new one.

    Operates on lines rather than on a parsed dict precisely so that comments,
    blank lines, ordering and unrelated keys survive untouched.
    """
    wanted = {normalise(name) for name in target_names}
    lines = text.splitlines()
    replaced = False
    out: list[str] = []

    for raw in lines:
        stripped = raw.strip()
        if not replaced and stripped and not stripped.startswith("#") and "=" in stripped:
            raw_key, _, _ = stripped.partition("=")
            if normalise(raw_key) in wanted:
                # Keep the spelling already in the file. Rewriting the name as
                # well as the value would leave the user's chosen convention
                # quietly changed underneath them.
                out.append(f"{raw_key.strip()}={value}")
                replaced = True
                continue
        out.append(raw)

    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{write_name}={value}")

    return "\n".join(out) + "\n"


def _write_atomic_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".env-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        # Windows ACLs do not map onto POSIX modes; the profile directory is
        # already per-user there, so a failure here is not a leak.
        with contextlib.suppress(OSError):
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def set_api_key(value: str, path: Path | None = None) -> bool:
    """Store or clear the key in .env. False when it could not be written."""
    target = path or paths.env_file()
    cleaned = value.strip()
    try:
        existing = target.read_text(encoding="utf-8")
    except OSError:
        existing = ""

    try:
        spliced = _splice(existing, API_KEY_ALIASES, cleaned, DEFAULT_KEY_NAME)
        _write_atomic_private(target, spliced)
    except OSError as exc:
        log.warning("could not write %s: %s", target, exc)
        return False

    log.info("API key %s in %s", "stored" if cleaned else "cleared", target.name)
    return True


def looks_like_api_key(value: str) -> bool:
    """A cheap shape check, so the UI can reject a paste error before a 401.

    Deliberately loose: Fathom does not document a key format, and a check that
    is stricter than the truth would reject a valid key with no way past it.
    """
    cleaned = value.strip()
    return len(cleaned) >= 16 and bool(re.fullmatch(r"[A-Za-z0-9._\-]+", cleaned))
