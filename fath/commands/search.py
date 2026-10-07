"""`fath search` -- full-text search across every synced transcript."""

from __future__ import annotations

import sys

from fath import db

HELP = """usage: fath search <words...> [--limit n]

Searches transcripts that have already been synced. Nothing is fetched.
"""


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(HELP)
        return 0 if argv else 2

    limit = 20
    terms = []
    index = 0
    while index < len(argv):
        if argv[index] == "--limit" and index + 1 < len(argv):
            try:
                limit = int(argv[index + 1])
            except ValueError:
                print("fath search: --limit needs a number", file=sys.stderr)
                return 2
            index += 2
            continue
        terms.append(argv[index])
        index += 1

    query = " ".join(terms).strip()
    if not query:
        print("fath search: nothing to search for", file=sys.stderr)
        return 2

    hits = db.shared().search(query, limit)
    if not hits:
        print(f"no matches for {query!r}")
        return 0

    for hit in hits:
        when = (hit.get("started_at") or "")[:10]
        print(f"{when}  {hit.get('title') or 'untitled'}  [{hit.get('timestamp') or ''}]")
        print(f"    {hit.get('speaker') or '?'}: {(hit.get('snippet') or '').strip()}")
        print(f"    {hit.get('folder') or ''}")
    print(f"\n{len(hits)} match(es)")
    return 0
