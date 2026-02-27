# Test/Demo Mode — Cached Data for UI Development

## Goal

Add a "test mode" that serves a pre-cached report on Render so we can iterate on the UI/UX without waiting for the full analysis pipeline to run. The homepage should load instantly with cached data and include a link to view the full report.

---

## Context

The current flow requires submitting a playlist URL, waiting for the pipeline to call 8+ APIs for every artist (Spotify, MusicBrainz, Deezer, Genius, Discogs, Last.fm, Setlist.fm, YouTube), running the decision tree, and then rendering the report. This takes minutes. When we're just iterating on CSS, component layout, colors, or data display — we don't need any of that. We need instant page loads with real data.

---

## What to Build

### 1. Capture a Cache Snapshot

After the next successful full analysis run, save the complete report JSON to a static file:

```
data/demo/cached_report.json
```

This file should contain the **exact same JSON structure** that the frontend currently receives from the live analysis endpoint — playlist metadata, summary stats, verdict breakdown, threat categories, and the full `artists[]` array with all evidence, radar data, scores, and signals.

If a cached report already exists from a previous run (check for any `playlist_*_results.json` or report JSON files in the `data/` directory), use that instead of running a new analysis. The point is to avoid running the pipeline.

**Fallback:** If no cached data exists yet, generate a snapshot from the most recently completed report. Check the database or file system for the latest report data (e.g., report ID `c7313d0b` or whatever the most recent successful run was) and serialize it to `data/demo/cached_report.json`.

### 2. Add a `/demo` Route

Create a new route that serves the cached data directly:

```
GET /demo
```

This route should:
- Read `data/demo/cached_report.json` from disk
- Pass it directly to the same report template/component used by `/report/{id}`
- Skip all API calls, database lookups, and analysis logic entirely
- Render the full report page identically to a live report

The report page rendered at `/demo` should look and behave **exactly** like `/report/{id}` — same summary section, same artist cards, same expandable details, same radar charts. The only difference is the data source (static file vs live pipeline).

### 3. Add a Demo Banner

At the top of the `/demo` report page, add a small dismissible banner:

```
⚡ Demo Mode — Viewing cached report data. Results may not reflect the latest analysis.
```

Style it subtly — muted background, small text, not intrusive. Include a dismiss/close button (×).

### 4. Update the Homepage

On the main landing page (wherever the playlist URL input form lives), add a link below the form:

```
—— or ——

View a sample report →
```

The "View a sample report →" link should point to `/demo`. Style it as a secondary/muted link — not competing visually with the main CTA (the analyze button), but clearly visible.

### 5. Environment Variable Toggle (Optional but Recommended)

Add an env var to control whether demo mode is available:

```
DEMO_MODE_ENABLED=true
```

When `false`, the `/demo` route returns a 404 and the homepage link is hidden. This lets us disable it cleanly in production later without removing code.

---

## Implementation Notes

- **Do NOT create fake/synthetic data.** The demo must use real analysis output from a real playlist run. The whole point is to see how the UI renders with actual data — edge cases, missing fields, long artist names, Unicode characters, etc.
- **The cached JSON file should be committed to the repo** (or stored in a persistent location on Render). It doesn't change — it's a static snapshot for development purposes.
- **Keep the same component tree.** The `/demo` route should reuse 100% of the report rendering code. Do not create a separate "demo report" component. The data flows in the same way; only the source changes.
- **Artist count:** The cached report should include all artists from the scanned playlist (aim for 60-120+ artists including both analyzed and skipped/timed-out ones). This is important for testing scroll performance, pagination behavior, and how the UI handles large lists.
- **All existing report features must work:** expandable artist cards, radar charts, clickable source links, signal color coding, verdict breakdown bar, threat category display — everything. The demo is a UI testing surface, so every feature needs to be exercisable.

---

## File Changes Summary

| File | Change |
|------|--------|
| `data/demo/cached_report.json` | New file — static snapshot of a completed report |
| Route handler (Flask/app.py or equivalent) | Add `GET /demo` route that reads cached JSON and renders report |
| Report template/component | No changes — reuse as-is |
| Homepage template/component | Add "View a sample report →" link below the analyze form |
| Config/environment | Add `DEMO_MODE_ENABLED` env var (default `true`) |

---

## Acceptance Criteria

1. Visiting `/demo` on Render loads a full report page instantly (no loading spinner, no API calls)
2. The demo report is visually identical to a live `/report/{id}` page
3. All interactive features work (expand/collapse cards, radar charts render, links are clickable)
4. The homepage has a visible "View a sample report" link that navigates to `/demo`
5. The demo banner appears at the top and can be dismissed
6. No analysis pipeline code runs when loading `/demo`
