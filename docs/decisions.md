# Decisions

The "why" register. Every non-obvious choice gets an entry, and code comments
reference these by name.

## Rate limiting retries, rather than a time-boxed cache

A time-boxed cache is right for an optional status read, where a stale copy
beats no copy. It is wrong here. A bulk sync has no cache to fall back on, and
a dropped page means missing meetings — the one outcome the tool exists to
prevent. `fath/ratelimit.py` therefore retries, with full jitter and a cap.

## The pace is bounded by the reset window, not by `maxBackoffSec`

Those are different ideas and conflating them broke a test in a way that would
have been invisible in production. `maxBackoffSec` caps how long to wait *after a
failure*; spacing requests is not a failure, and its natural ceiling is the
window itself. Waiting longer than a whole window to send one request is never
right.

## The rate limit is 10 requests per 60 seconds

Measured, not documented. Fathom describes the `RateLimit-*` headers but
publishes no number anywhere. Observed headers report `10` with a 60-second
window.

This is why `api.minIntervalSec` defaults to 1.0 and not 0.4: a 0.4s floor spends
the entire window in four seconds and then stalls for the other fifty-six.
Throughput is identical, but every window ends pressed against the limit with no
room for a retry. The limiter instead paces at `reset / (remaining - reserve)`.

## A Scheduled Task, not the `HKCU\...\Run` key

The Run key fires once at logon; this needs to repeat through the day.
`-StartWhenAvailable` also covers the laptop that was asleep at 08:00, which a
Run key cannot.

## `web/dist` is committed

The backend is Python, and committing the bundle is what makes `fath web` work
with no node, no `npm install` and no network. Node is needed only to *change*
the UI.

## Inline SVG icons, not the Material Symbols webfont

You asked for Material icons; the full outlined woff2 is 3.8 MB for thousands of
glyphs and this app uses about forty. Because `web/dist` is committed, shipping
the font would put a 3.8 MB binary into git and re-churn it on every rebuild.

Subsetting was tried first and does not work cleanly: this font resolves icon
names through chained `rclt`/`rlig` substitutions, and fontTools' closure either
drops the icons entirely (2 kB, letters only) or keeps nearly all of them
(2.6 MB). `web/scripts/build-icons.mjs` inlines the path data instead — 14 kB,
no font loading, and it cannot fail on a slow connection.

The icon list is scanned out of the source rather than hand-maintained, in two
tiers: `name="check"` is required and a missing SVG fails the build, while any
other lowercase string is a candidate kept only if an SVG exists. The loose tier
is what catches icons chosen in a ternary; the validation is what stops
`cost: "free"` from breaking the build, which it did.

## The path-length cap is derived, not configured

The folder name appears **twice** in the deepest path — once as the directory and
once as the filename prefix. A flat 120-character cap produced a 292-character
path under an ordinary Documents folder, which Windows rejects.

    cap = (260 - len(output_root) - 2 - len("_action-items.md") - 8) // 2

`naming.maxFolderNameLen` is a ceiling the user may lower, never the operative
number. MAX_PATH counts the terminating null, so a path of exactly 260 still
fails; the guard is `>=`.

## The collision suffix is an argument, not something the caller appends

Appending it afterwards can overflow MAX_PATH. Two same-day meetings collide;
adding a recording-ID suffix exceeds the budget; the folder is created (short
enough on its own) and the write inside it fails with a bare `FileNotFoundError`
naming a path that looked perfectly fine. The length cap has to see the final
name.

## The content hash records what the API sent, not what we ended up with

The per-recording fallback mutates the meeting by filling in a null transcript.
Re-hashing after that stored a value the next run's raw payload could never
match, so those meetings were rewritten on **every** sync forever, wasting API
requests against a tight rate limit. The hash is now taken from the incoming
payload and carried through.

The hash also deliberately ignores `share_url` and `crm_matches`: Fathom returns
a fresh signed URL on every call, so hashing them would mark unchanged meetings
changed nightly.

## Search has two strategies

One FTS row is one spoken line, which is what gives a result its speaker and
timestamp. But FTS5 reads a bare multi-word query as "all these words in the same
row", so `commercial pipeline` returns **zero** hits when those two words are
almost never in one short utterance.

A single word or a `"quoted phrase"` is matched directly. Several bare words
instead select meetings where *every* word appears somewhere, then return the
lines matching any of them. Multi-word search was tried as a fallback first, and
that was wrong: one lucky same-line hit hid every meeting where the words were
spread out, which is most of them.

## The settings root is found through a pointer file, not an environment variable

A Scheduled Task inherits the *logon* environment, so `FATH_HOME` exported in a
shell — or set with `setx` and no logoff — never reaches it. A scheduled run and
an interactive run resolving different roots would sync into two places and
neither would look wrong.

`FATH_HOME` still works, as the natural override for a one-off.

## The API key is spliced into `.env`, never written whole

The file also holds the webhook secret and hand-typed comments. Rewriting it from
a parsed dict discards both silently. Never whole-file-copy a file its own owner
also edits — splice it.

Key names are folded (`-` to `_`, upper-cased) because the dashed spelling
**cannot** be a real environment variable: `export FATHOM-PERSONAL-API-KEY=x` is
a shell syntax error.

## The web server sends no CORS headers, on purpose

Their absence is load-bearing: it is what stops a page on another origin reading
the token out of the served HTML. The Host-header allowlist defeats DNS
rebinding, where an attacker's domain resolves to 127.0.0.1. A token is required
on every mutating route.

The token matters separately from the Host check: a cross-site form POST carries
the *target* Host, so any page you visit could otherwise start a sync or
overwrite your key. Comparison uses `secrets.compare_digest`.

The path-traversal guard runs **before** the bundle-exists check. A security
guard that only applies once something else is true is a guard that stops
applying the day that changes.

## Progress is Server-Sent Events, not a WebSocket

This stream is one-way and the server is stdlib Python. SSE also replays from
`run_events`, so closing the tab mid-sync loses nothing.

Two details in the hand-rolled implementation are load-bearing: the connection
is closed explicitly, because HTTP/1.1 without a `Content-Length` leaves the
browser waiting forever; and every write can raise once the tab closes, which is
a normal end of stream rather than an error worth logging.

## The shim lives in this repo

`scripts/fath-shim.ps1` sits here, with `scripts/install-shim.ps1` splicing a
marker block that dot-sources it. The launcher ships with the tool it launches,
and a fix arrives with `git pull` rather than a profile rewrite. The installer
targets `$PROFILE` by default, or any file passed as `-ProfilePath`.

`fath.cmd` in the clone root runs the same entry point with no install step, so
a fresh clone works before anyone finds the installer. With no arguments it
opens the dashboard rather than printing help: it is the front door, and the
dashboard is what someone typing a bare command wants. Making `fath` itself
default to `web` was rejected; the shim's bare `fath` and scripted callers keep
the conventional help. Adding the clone to `PATH` was rejected for the reason
`main.py` avoids `PYTHONPATH`: a session-wide change for a one-shot need.

## Examples, credentials, and local data

Public examples use invented identities and meeting details. Keep punctuation,
duplicate first names, suffix lengths, and path constraints in regression
fixtures. Deleting the fixtures was rejected because it would discard coverage
for naming defects.

API requests reject all redirects. urllib's default redirect handler preserves
custom headers, including X-Api-Key, when changing origins. Rejecting redirects
is simpler to audit than selectively trusting destinations, and the documented
Fathom endpoints do not require redirects.

Every POST requires the local token, including previews and key verification.
A hand-maintained route allowlist was rejected because new routes silently
escaped protection. Dashboard responses deny framing, disable caching, and omit
referrers: CORS alone does not stop clickjacking or cached token-bearing HTML.

Git exclusions cover environment variants, private key files, runtime state,
and generated meeting exports. Relying only on `.env` missed backup files and
exports written elsewhere. Exclusions do not remove files already tracked or
retained in Git history.
