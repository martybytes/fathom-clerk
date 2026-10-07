# fathom-helper agent rules

Process rules. Architecture and invariants are in `CLAUDE.md`; the two are
deliberately separate.

## Gates

One command, and it must pass before anything is committed:

```powershell
.\scripts\check.ps1            # everything
.\scripts\check.ps1 -Quick     # skips the slow end-to-end suite
.\scripts\check.ps1 -Fix       # apply ruff autofixes first
```

It runs ruff, ruff-format, mypy, pytest with coverage, `tsc`, and a check that
the committed `web/dist` is not older than its sources. Missing tooling is
reported as `skip`, never silently passed over: a gate you believe is running
and is not is worse than no gate.

The individual commands still work if you want one of them:

```
python -m ruff check fath tests
python -m ruff format --check fath tests
python -m mypy
python -m pytest tests/ --cov
cd web; npm run typecheck
```

**Never weaken a gate to make it pass.** Do not skip, xfail, delete or loosen a
test to get green. If a test fails, either the code is wrong or the test's anchor
moved — repoint the anchor, keeping the rule it enforces. The coverage floor
ratchets up and never down.

## Tests grow with the code

Every change adds the test that would have caught the bug it fixes, in the layer
that would have caught it. Unit tests did not catch the folder-collision crash
because nothing exercised config, reimport, fetch and write together over
folders that already existed -- that is what `tests/test_e2e.py` is for.

- a new setting -> assert it round-trips and appears in `schema.snapshot()`
- a new route -> assert its auth, its success shape and its error shape
- a new naming rule -> a case in `tests/test_naming.py` that names the meeting
- a bug -> a regression test whose docstring says what actually went wrong

`tests/test_isolation.py` guards the suite itself. It exists because a test once
wrote a pointer file into the real %LOCALAPPDATA% and redirected later runs
into a pytest temp directory.

## Every behaviour-changing turn

- Add a `CHANGELOG.md` entry under `[Unreleased]`.
- Record any non-obvious choice in `docs/decisions.md`, naming the rejected
  alternative. Code comments reference those entries.
- Rebuild `web/dist` if `web/src` changed. The bundle is committed on purpose,
  and a stale one is worse than a missing one because it looks like it works.
- Regenerate `web/src/components/icons.ts` (`npm run icons`) after adding an
  icon; the build does this automatically.

## Branches

One branch per phase, named for the work (`feat/…`, `fix/…`, `docs/…`). Merge
with `git merge --no-ff`. Never commit straight to `main`, never force-push.

## Things that will bite

- `poc/` and `.env` are gitignored. `.env` holds a live key and the webhook
  secret; the key is spliced one line at a time, never rewritten whole.
- The `fath/naming.py` rules encode seven defects documented in
  `docs/naming.md`. Do not rewrite them from the docstring — read that file first.
- Nothing under `fath/ui/` or `fath/server/` may write settings directly. There
  is one writer, `config.set()`, and `tests/test_ui.py` enforces it.
- Splice into `$PROFILE`; never rewrite the file whole. The profile is a file
  its owner also edits.
