# fathom-helper

Architecture and invariants. Process rules are in `AGENTS.md`.

## Shape

Runs from the clone; nothing is installed or published. `fath/main.py` is the
entry point, `fath/cli.py` dispatches from `fath/commands.conf`, and each
subcommand is a module in `fath/commands/` exporting `main(argv) -> int` and a
`HELP` string.

The core — naming, the API client, the limiter, sync, the web server — is
**stdlib only**, so an unattended scheduled sync depends on nothing. Textual is
required by `fath ui` alone and degrades with a readable message.

## Invariants

- **One writer.** Every persisted setting goes through `config.set()`. Two
  writers is how a settings file loses edits the other writer never saw.
- **One declaration.** Settings are declared in `fath/schema.py` and rendered
  from there by the CLI, the terminal UI and the web UI. A setting added once
  appears in all three.
- **One event stream.** `sync.SyncEngine` yields `SyncEvent`; every front end
  consumes it rather than re-deriving progress.
- **The folder name matches `[a-z0-9_-]`.** Enforced at the end of
  `build_folder_name`, which raises rather than returning something unsafe.
- **The path cap is derived from the output root**, never read from a setting.
  The name appears twice in the deepest path.
- **The content hash is of what the API sent**, not of what we ended up with
  after the fallback filled anything in.

## Rate limiting

10 requests per 60 seconds, measured from the response headers. The limiter
paces at `reset / (remaining - reserve)` and stops while the reserve is unspent,
so a 429 is the exception rather than the mechanism. Verify with `--debug`:
`RateLimit-Remaining` should never reach zero.

## The web server

Loopback bind, no CORS headers at all, a Host-header allowlist, and a token on
every mutating route. Each of those is asserted in `tests/test_server.py`; none
is decorative. `docs/decisions.md` explains what each one defeats.

## Reading order for anything naming-related

`docs/naming.md` first. It records seven defects that a plausible fixture suite
can still miss.
