# Naming rules

How a meeting becomes a folder. People, companies, titles, dates, and recording
IDs below are fictional. Regression fixtures preserve the failure conditions
without reproducing private meetings.

## API behavior that naming depends on

Observed rate-limit headers reported 10 requests per 60-second window. Treat
this as an observation, not a provider guarantee; adapt to response headers.
Pace using `reset / (remaining - reserve)` instead of spending the window in a
burst. Keep a reserve and honor 429 retry guidance.

The meeting-list include flags return transcripts, summaries, action items, and
highlights together. Prefer one request per page instead of separate requests
for each artifact. Missing transcripts and summaries may genuinely be empty:
per-recording fallback can also return an empty response.

## Seven naming defects

1. **Orphaned stopwords.** `Jules & Marley 1:1` became `and-1-on-1` after
   attendee names were stripped. Trim stopwords only from the title's edges.
2. **Recorder missing from the strip set.** In `Jules/Marley R&D Updates`, the
   recorder may not be an invitee. Include the recorder when stripping names.
3. **Mid-word truncation.** Cutting `example-planning-meeting` midway through
   `meeting` changes its meaning. Cut at word boundaries, then trim trailing
   stopwords.
4. **Collision suffix outside the cap.** Appending a recording ID after the
   length check can make the final file path too long. Pass the suffix into
   `build_folder_name` so it participates in the budget. Regression tests use
   the synthetic nine-digit ID `123456789` to preserve suffix length.
5. **Email addresses as display names.** `parker@example-labs.example` must
   become `parker`, not a slug containing the domain. Take the local part and
   split on `.`, `_`, and `+`.
6. **Only shrinking the title.** Once the title reaches its minimum length,
   long attendee lists can still overflow. Remove attendees one at a time
   after the title reaches its floor.
7. **MAX_PATH off by one.** Windows MAX_PATH includes the terminating null:
   259 characters are usable, and a path of exactly 260 must be rejected.

## Path budget

The folder name appears twice in the deepest path: directory and filename.
Derive the cap from the output root and longest suffix, reserving safety space:

```python
cap = (260 - len(output_root) - 2 - len("_action-items.md") - 8) // 2
```

`maxFolderNameLen` is a user-configurable ceiling, not a replacement for this
calculation. Keep collision suffixes inside the cap. Preserve the regression
cases when changing naming code.

## Other design choices

Recorder names can appear in many folders; `excludeRecorder` controls this.
Resource accounts such as `Conference Room *` and `Account Management` are
filtered separately from people. Same-day repeat meetings use a recording-ID
suffix: an ordinal would look simpler but depend on processing order.

Offline tests use fictional payloads and never require an API key or private
exports.
