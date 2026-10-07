# Changelog

## [Unreleased]

### Added
- Logo and favicon set (`favicon.svg`, `favicon.ico`, `apple-touch-icon.png`)
  for the web dashboard, with the logo in its header and the README.
- Hero banner on the dashboard overview and in the README.
- Illustrated empty states for the meeting list (no meetings yet) and the idle
  sync log. `Empty` takes an optional decorative `image`.

## [0.1.0] - 2026-10-07

### Added
- `fath` command with sync, status, search, config, key, init, doctor, ui, web
  and schedule subcommands, dispatched from `fath/commands.conf`.
- Sync engine: cursor pagination, adaptive rate limiting, per-meeting commits,
  skip-unchanged via a content hash, and re-import of folders already on disk.
- SQLite store with FTS5 transcript search, run history and replayable events.
- Settings declared once in `fath/schema.py`, rendered by all three front ends.
- Textual terminal dashboard (`fath ui`).
- React web dashboard (`fath web`), loopback-only with token-protected writes.
- Windows Scheduled Task installer, and a PowerShell shim for the `fath` command.

### Security
- Reject API redirects so credentials cannot be forwarded to another destination.
- Require the local token on every POST, including key verification and naming
  previews.
- Prevent dashboard framing, browser caching, and referrer disclosure.
- Exclude environment backups, private keys, runtime databases, logs, settings,
  and meeting exports from Git.

### Fixed
- The content hash was taken after the per-recording fallback filled in a null
  transcript, so those meetings were rewritten on every sync forever.
- Multi-word transcript search returned nothing: one FTS row is one spoken line,
  and FTS5 reads a bare multi-word query as "all of these in the same row".
- Seven naming defects; see `docs/naming.md`.

### Notes
- The Fathom rate limit is 10 requests per 60 seconds. Measured, not documented.
