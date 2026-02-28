# Fix: Retry Scan Infrastructure & Summary Metrics Separation

**Date:** 2026-02-27  
**Priority:** Critical — retry scan is broken, summary metrics are misleading  
**Scope:** Backend (`app.py`, `audit_runner.py`) + Frontend (report summary UI)

---

## Problem Summary

Two distinct bugs that need to be fixed together:

### Bug 1: Retry scan crashes immediately
The `retry_skipped_artists()` function in `audit_runner.py` instantiates `SpotifyClient()` without passing the required `config` argument. The traceback is:

```
File "/opt/render/project/src/spotify_audit/audit_runner.py", line 1056, in retry_skipped_artists
    client = SpotifyClient()
TypeError: SpotifyClient.__init__() missing 1 required positional argument: 'config'
```

The `config` object IS available — it's passed into `retry_skipped_artists()` as a parameter (visible in `app.py` line 564). The function just doesn't forward it to `SpotifyClient()`.

### Bug 2: Skipped artists pollute the summary metrics
Currently, the 56 unskipped artists show up in the summary bar at the top of the report, inflating the total count and skewing the verdict breakdown. Per the UI spec, skipped artists should NOT be counted in the analysis metrics. They should appear as a separate informational notice — "56 artists could not be scanned" — but NOT inside the verdict breakdown percentages or the analyzed count.

---

## Fix 1: Backend — `SpotifyClient` initialization in `retry_skipped_artists()`

### File: `spotify_audit/audit_runner.py`

**Find** the `retry_skipped_artists` function (around line 1056). Look for:

```python
client = SpotifyClient()
```

**Replace with:**

```python
client = SpotifyClient(config)
```

That's the critical one-line fix. The `config` parameter is already being passed into `retry_skipped_artists()` from `app.py` — it's just not being forwarded to the client constructor.

### Verify the function signature

Confirm the function signature looks like this (or similar):

```python
def retry_skipped_artists(skipped, config, on_progress=None):
```

The `config` param is there. It's just not being used when constructing `SpotifyClient`.

### Secondary check: Look for other bare `SpotifyClient()` calls

Search the entire codebase for any other instance of `SpotifyClient()` being called without `config`:

```bash
grep -rn "SpotifyClient()" src/
```

Any hit that does NOT pass `config` (or equivalent) is the same bug. Fix every one. The correct pattern is always `SpotifyClient(config)`.

---

## Fix 2: Backend — Make retry results merge back into the original scan

After fixing the `SpotifyClient` initialization, verify the retry flow works end-to-end. The function `retry_skipped_artists()` should:

1. Accept the list of `skipped` artists (the ones that timed out or errored during the original scan)
2. Create a new `SpotifyClient(config)` ← **the fix**
3. Iterate through each skipped artist and attempt to scan them using the same Phase 1 (Collect) → Phase 2 (Evaluate) pipeline as the original scan
4. Return two things:
   - `artist_reports`: list of successfully scanned artist results
   - `still_skipped`: list of artists that STILL failed on retry

### Verify in `app.py` (`_run_retry_background`)

After the call to `retry_skipped_artists()` returns, `app.py` should merge the successful retry results back into the original scan data. Check that:

```python
artist_reports, still_skipped = retry_skipped_artists(
    skipped=skipped,
    config=config,
    on_progress=on_progress,
)
```

After this call, confirm the code:
- Appends `artist_reports` into the original scan's artist list
- Updates the scan's summary/verdict counts to include the newly scanned artists
- Updates the `skipped_artists` list to only contain `still_skipped`
- Saves/caches the updated results so the frontend picks them up

If any of this merging logic is missing, it needs to be added. The retry results should seamlessly integrate — the user reloads the report page and sees more artists analyzed, fewer skipped.

---

## Fix 3: Frontend — Separate skipped artists from summary metrics

This is the UI change. The summary bar at the top of the report currently includes skipped artists in its counts. It should not.

### Current (broken) behavior
- Summary says "122 Artists" and includes skipped ones in the verdict bar
- Skipped artists appear in the verdict breakdown percentages
- The "56 artists could not be scanned" message sits awkwardly in the summary area

### Target behavior

#### Summary metrics section (the stat cards and verdict bar)
- Show ONLY analyzed artists: **"69 Analyzed"** (not 122)
- Verdict breakdown bar percentages are calculated from the 69 analyzed artists ONLY
- The gray "Not Scanned" segment in the verdict bar should be VISUALLY present (so users see the full playlist scope) but NOT counted in the percentage labels
- Threat breakdown counts should only reference analyzed artists

#### Skipped artists notice (separate from summary)
Render a distinct, clearly separated notice below the summary section. NOT inside the summary metrics. It should say:

```
⚠ 56 artists could not be scanned
These artists were skipped due to timeouts or errors during scanning.
They are not included in the analysis above.
[Retry Scan →]
```

Style: Use the gray color `#9ca3af` for the notice background. It should be informational, not alarming. The "Retry Scan →" button triggers the retry flow.

### Implementation details

#### In the scan results JSON (backend)

The JSON structure should clearly separate analyzed vs skipped:

```json
{
  "summary": {
    "total_playlist_artists": 122,
    "analyzed_count": 69,
    "skipped_count": 56,
    "verdict_breakdown": {
      "verified_artist": 12,
      "likely_authentic": 25,
      "inconclusive": 8,
      "suspicious": 14,
      "likely_artificial": 10
    }
  },
  "artists": [ /* only the 69 analyzed artists */ ],
  "skipped_artists": [
    {
      "artist_id": "abc123",
      "artist_name": "Some Artist",
      "skip_reason": "timeout",
      "error_message": "API call timed out after 30s"
    }
  ]
}
```

Key points:
- `verdict_breakdown` numbers sum to `analyzed_count` (69), NOT `total_playlist_artists` (122)
- `artists` array contains ONLY successfully analyzed artists
- `skipped_artists` is a separate top-level array

#### In the frontend report component

**Verdict bar rendering:**

```
[===Verified===|==Likely Authentic==|=Inconclusive=|==Suspicious==|=Artificial=|░░Not Scanned░░]
```

- The colored segments (Verified through Artificial) use widths proportional to the FULL playlist (122)
- The gray "Not Scanned" segment fills the remainder
- Percentage labels on the colored segments are calculated from analyzed_count only: e.g., "Verified: 17%" means 12/69, NOT 12/122
- The gray segment label says "56 Not Scanned" (count only, no percentage)

**Summary stat card:**

```
69 Analyzed
out of 122 artists in playlist
```

NOT "122 Artists". The primary number is always the analyzed count.

#### Skipped artists list (collapsible, below summary)

If the user clicks to expand the skipped artists notice, show a simple list:

```
⚠ 56 artists could not be scanned

▼ View skipped artists

  Artist Name 1 — timeout
  Artist Name 2 — API error
  Artist Name 3 — timeout
  ...

[Retry Scan →]
```

Each skipped artist shows: name + reason (timeout, error, rate limit, etc.)

The list should be collapsible (collapsed by default) so it doesn't dominate the page.

---

## Fix 4: Retry button UX flow

When the user clicks "Retry Scan →":

1. Button changes to a loading state: "Retrying 56 artists..." with a spinner
2. Progress updates as artists complete: "Retrying... 12/56 complete"
3. When done, one of two outcomes:
   - **All recovered:** Notice disappears entirely. Summary metrics update to show the full count. Page refreshes/updates to show all artists.
   - **Partial recovery:** Notice updates: "⚠ 23 artists still could not be scanned (33 recovered)" and a "Retry Again →" button appears. Summary metrics update to include the 33 recovered artists.
   - **None recovered:** Notice updates: "⚠ Retry failed — 56 artists still could not be scanned. This may be due to API outages. Try again later." No retry button (prevent infinite retry loops — maybe allow one more attempt after a cooldown).
4. The page should NOT navigate away. Results merge into the current report view.

---

## Fix 5: Backend — Ensure `config` is constructed correctly for retry context

In `app.py`, the `_run_retry_background` function constructs the `config` to pass to `retry_skipped_artists`. Verify this config object includes everything `SpotifyClient` needs:

```python
# In app.py, wherever config is built for the retry:
config = Config(
    spotify_client_id=os.environ.get("SPOTIFY_CLIENT_ID"),
    spotify_client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET"),
    # ... all other required fields
)
```

Check the `SpotifyClient.__init__` signature to see exactly what it expects from `config`. Common required fields:
- `spotify_client_id`
- `spotify_client_secret`
- Possibly: `timeout`, `max_retries`, API keys for other services

If the retry path is constructing a different/incomplete config compared to the initial scan path, that could cause secondary failures even after fixing the missing argument.

---

## Testing Checklist

After implementing all fixes:

- [ ] `grep -rn "SpotifyClient()" src/` returns zero results (all calls pass config)
- [ ] Trigger a scan on a large playlist that produces skipped artists
- [ ] Verify the report summary shows "N Analyzed" (not total playlist count)
- [ ] Verify verdict bar percentages are based on analyzed count only
- [ ] Verify the skipped artists notice appears below the summary, not inside it
- [ ] Click "Retry Scan" and verify it doesn't crash with the `TypeError`
- [ ] Verify retry results merge back into the report (recovered artists appear in the artist list)
- [ ] Verify `still_skipped` count updates after retry
- [ ] Verify the verdict breakdown recalculates to include recovered artists
- [ ] On mobile: verify the skipped notice doesn't break layout

---

## Files to Modify

| File | Change |
|------|--------|
| `spotify_audit/audit_runner.py` ~line 1056 | `SpotifyClient()` → `SpotifyClient(config)` |
| `spotify_audit/audit_runner.py` | Verify `retry_skipped_artists` returns `(artist_reports, still_skipped)` correctly |
| `web/app.py` `_run_retry_background` | Verify config construction is complete; verify merge logic for retry results |
| Frontend: report summary component | Split summary metrics to use `analyzed_count` only; render skipped notice separately |
| Frontend: verdict bar component | Calculate percentages from `analyzed_count`; render gray segment from `skipped_count` |
| Frontend: skipped artists notice | New component: collapsible list with retry button, positioned below summary |
| Backend: scan results JSON builder | Ensure `summary.analyzed_count`, `summary.skipped_count`, and `skipped_artists[]` are distinct fields |
