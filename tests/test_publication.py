"""Public release exclusions are checked with Git's own matching rules."""

import shutil
import subprocess
from pathlib import Path


def test_private_artifacts_are_ignored(tmp_path: Path) -> None:
    """Environment backups, exported meetings and local state were easy to stage accidentally."""
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / ".gitignore", tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    private = [
        ".env",
        ".env.backup",
        ".env.production",
        ".env-123.tmp",
        "private.pem",
        "private.key",
        "state/fathom.db",
        "state/fathom.db-wal",
        "state/data.sqlite3",
        "state/data.sqlite3-shm",
        "logs/run.log",
        "logs/run.log.1",
        "state/data.sqlite-journal",
        "meetings/.fath.json",
        "home.path",
        "token",
        "config.json",
        "local.json",
        "poc/out/meeting.json",
        "poc/fetch_all.py",
        "poc/FINDINGS.md",
        "GATES.md",
        "docs/release-audit.md",
        "meetings/sample_meeting.json",
        "meetings/sample_transcript.md",
        "meetings/sample_summary.md",
        "meetings/sample_action-items.md",
    ]
    result = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", "--stdin", "-z"],
        input="\0".join(private) + "\0",
        text=True,
        capture_output=True,
        check=True,
    )
    assert set(result.stdout.rstrip("\0").split("\0")) == set(private)
    for public in (
        ".env.example",
        "fath/api.py",
        "tests/test_publication.py",
        "docs/naming.md",
        "docs/decisions.md",
    ):
        result = subprocess.run(
            ["git", "-C", str(tmp_path), "check-ignore", public],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1, public
