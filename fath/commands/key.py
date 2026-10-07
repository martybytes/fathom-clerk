"""`fath key` -- set, clear or check the Fathom API key."""

from __future__ import annotations

import getpass
import sys

from fath import config, keystore
from fath.api import ApiError, AuthError, FathomClient

HELP = """usage: fath key [--set | --clear | --check]

Read order is the environment, then the repo .env. Writing goes to .env, and
splices the one line rather than rewriting the file, so other keys and comments
survive.

Generate a key at https://fathom.video/customize#api-access-header
"""


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "--show"
    if action in ("-h", "--help"):
        print(HELP)
        return 0

    if action == "--clear":
        ok = keystore.set_api_key("")
        print("cleared" if ok else "could not write .env")
        return 0 if ok else 1

    if action == "--set":
        # getpass so the key is not echoed, and never goes into shell history.
        value = getpass.getpass("Fathom API key: ").strip()
        if not value:
            print("nothing entered", file=sys.stderr)
            return 2
        if not keystore.looks_like_api_key(value):
            print(
                "that does not look like an API key (too short, or odd characters)", file=sys.stderr
            )
            return 2
        if not keystore.set_api_key(value):
            print("could not write .env", file=sys.stderr)
            return 1
        print(f"stored in {keystore.paths.env_file()}")
        return 0

    described = keystore.describe()
    if not described["set"]:
        print("no API key found")
        print(f"  looked in : the environment, then {described['envFile']}")
        print("  set one with: fath key --set")
        return 1
    print(f"api key: set, from {described['source']} (...{described['tail']})")

    if action == "--check":
        value, _ = keystore.resolve()
        client = FathomClient.from_config(value, config.shared())
        try:
            result = client.verify_key()
        except AuthError as exc:
            print(f"rejected: {exc}", file=sys.stderr)
            return 1
        except ApiError as exc:
            print(f"could not check: {exc}", file=sys.stderr)
            return 1
        print(f"works: {result['visibleMeetings']} meeting(s) on the first page")
    return 0
