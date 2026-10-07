# Security and privacy

fathom-helper stores meeting transcripts, summaries, attendee names, recording
links, and API payloads on your computer. Treat exports, the SQLite database,
logs, and backups as private data. Keep the output directory and settings home
outside your source checkout and cloud-shared folders unless you intend to share
that content.

The API key lives in your environment or local `.env`; `.env.example` contains
only empty placeholders. Never paste real keys, meeting content, or diagnostic
files containing private paths into public issues. If a key was committed,
revoke it and issue a replacement; deleting the file from the latest commit
does not remove it from Git history.

The dashboard binds to loopback and is intended for a trusted local account.
It is not a multi-user or remotely hosted service. Do not expose its port through
a proxy or tunnel. Browser protections include Host validation, a token on every
POST, no CORS access, and blocked framing. Local processes can access the service;
the token does not isolate mutually untrusted users on the same computer.

API clients reject redirects to prevent forwarding credentials. Changing
`api.baseUrl` changes where your key is sent; use only endpoints you trust.

## Before publishing a checkout

- Review `git status --short`, `git ls-files`, and all branches and tags.
- Run a secret scanner against both the working tree and full Git history,
  with output redaction enabled. Review test fixtures and generated assets:
  scanners can mistake dummy keys and SVG path data for credentials.
- Inspect examples, documents, screenshots, commit messages, and author email
  addresses for personal or customer information. Automated secret scans do not
  establish that names and meeting titles are fictional.
- Run `scripts/check.ps1`, `npm audit` in `web`, and `pip-audit` against
  `requirements-dev.txt` in a disposable environment.
- `.gitignore` prevents accidental staging only. It cannot remove already
  tracked files or clean historical commits. Never publish a ZIP of the whole
  working directory: it may contain ignored secrets and meeting exports.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting feature when enabled on the
published repository. Do not submit credentials, private recordings, or an
unpatched exploit containing private data in a public issue. If private
reporting is unavailable, request a private contact channel without disclosing
the sensitive details.
