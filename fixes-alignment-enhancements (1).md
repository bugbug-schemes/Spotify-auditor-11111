# Spotify Auditor — Fixes, Alignment & Enhancements

**Date:** 2026-02-27
**Companion to:** `ui-spec-round2.md` (the canonical UI spec)

This document covers everything needed to bring the system into alignment with the UI spec and fix critical bugs discovered during report audits. It is organized by priority: critical bugs first, then document alignment, then enhancements.

---

# PART A: CRITICAL — API Matching Failure

**Report:** `/report/a54043da` (Lava Lamp playlist)
**Severity:** Critical — the system is producing dangerously wrong results. No report should be treated as reliable until these are fixed.

## A.1 The Problem

The Lava Lamp report flags **23 of 35 analyzed artists as Suspicious: PFC Ghost Artist**. But this playlist contains well-known, critically acclaimed, thoroughly documented artists:

- **Caterina Barbieri** — Artistic Director of the Venice Music Biennale 2025–2026. Wikipedia article. Performed at Primavera Sound, Sonar, Barbican Centre. Has Bandcamp. 7 albums. System says: **"Suspicious, only found on 1 platform"** — 0 on Platform Presence, Fan Engagement, IRL Presence, Industry Signals.
- **Max Richter** — CBE-decorated British composer. Scored *Arrival*, *Ad Astra*, *The Leftovers*. Deutsche Grammophon artist. Wikipedia article. Glastonbury performer. 443,712 Deezer fans. System says: **"Suspicious, only found on 1 platform"**.
- **William Basinski** — legendary ambient composer. Wikipedia article. 100+ Discogs releases. MusicBrainz entry. System says: **"Suspicious, only found on 1 platform"**.
- **Brian Eno** (via "Roger Eno, Brian Eno") — one of the most famous musicians alive. System says: **"Suspicious, only found on 1 platform"**.
- **Biosphere** — Geir Jenssen, pioneering Norwegian ambient artist. Wikipedia, Discogs, MusicBrainz. System says: **"Suspicious, only found on 1 platform"**.
- **Mary Lattimore** — acclaimed harpist, NPR Tiny Desk performer. Wikipedia, Discogs, Bandcamp. System says: **"Suspicious, only found on 1 platform"**.
- **Hammock** — post-rock/ambient duo, 20+ years active. System says: **"Suspicious, only found on 1 platform"**.

**Every one of these artists exists on MusicBrainz, Discogs, Last.fm, Genius, Setlist.fm, and Wikipedia.** The system returns "not found" for all of them on all platforms except Deezer.

## A.2 Root Cause Analysis

The pattern across all 35 artists:
- **Deezer**: ✓ on nearly every artist
- **MusicBrainz**: ✗ on nearly every artist
- **Last.fm**: ✗ on most artists
- **Genius**: ✗ on every single artist
- **Discogs**: ✗ on every single artist
- **Setlist.fm**: ✗ on every single artist
- **Wikipedia**: ✗ on every single artist (Caterina Barbieri literally has a Wikipedia article)

**Every API failing except Deezer = systemic issue, not individual artist matching problems.** Likely causes:

1. **API calls timing out or erroring silently.** 61 of 96 artists timed out (63% failure rate). The system is probably treating timeouts/errors as "not found" rather than "could not check." This is the most dangerous bug — it turns missing data into false accusations.

2. **Name matching failure for multi-artist credits.** Several entries are comma-separated credits ("Roger Eno, Brian Eno", "Max Richter, Grace Davidson"). The system is probably searching for the full combined string rather than splitting on the separator and searching for the primary artist.

3. **Rate limiting across non-Deezer APIs.** MusicBrainz requires a User-Agent header and limits to 1 req/sec. If the system is hammering it without proper rate limiting, all requests may fail.

4. **Deezer works because it uses a different lookup path.** Deezer may use a direct ID mapping from Spotify rather than name search. All other APIs rely on name-based search which is failing.

## A.3 Required Fixes (in priority order)

### Fix 1: Separate "not found" from "error/timeout" (HIGHEST PRIORITY)

The system MUST distinguish three states:

| State | What happened | Scoring impact | UI display |
|---|---|---|---|
| **Found** | API queried, artist returned | Score per signal tables | ✓ (green, clickable link) |
| **Not found** | API queried successfully, artist absent | Negative points per scoring tables | ✗ (red X) |
| **Error / Timeout** | API call failed or timed out | ZERO points (no impact) | ⚠ (yellow/amber warning) |
| **Not checked** | API was skipped | ZERO points | — (gray dash) |

Currently all non-"found" states produce the same "✗ Not found" display and the same scoring penalty.

**Implementation:**

```python
class LookupResult:
    status: Literal["found", "not_found", "error", "timeout", "skipped"]
    data: dict | None
    error_message: str | None
    response_time_ms: int | None

# In evidence generation:
if result.status == "not_found":
    # Genuine absence — this IS a signal
    emit_evidence(type="red_flag", ...)
elif result.status in ("error", "timeout", "skipped"):
    # We don't know — this is NOT a signal
    emit_evidence(type="neutral", tag="api_error", ...)
    # Do NOT emit any red flag
```

**UI changes needed (add to ui-spec-round2.md checklist):**
- [ ] Platform checkmark row: add ⚠ state (yellow/amber) for "attempted but failed"
- [ ] Categories where all sources errored → gray (no data), NOT red (0)
- [ ] Verdict description: note API failures — "Evidence on {name} is incomplete — {N} data sources could not be reached"
- [ ] Collapsed card: ⚠ icon next to score when analysis is incomplete

### Fix 2: Diagnose API failures

Run a diagnostic on a known-good artist before any other work:

```python
# Test with artists we KNOW exist on every platform
for test_name in ["Radiohead", "Björk", "Brian Eno", "Caterina Barbieri"]:
    for api in [musicbrainz, lastfm, genius, discogs, setlistfm, wikipedia]:
        try:
            start = time.time()
            result = api.search(test_name)
            elapsed = time.time() - start
            print(f"{api.name}: {'FOUND' if result else 'NOT FOUND'} ({elapsed:.1f}s)")
        except Exception as e:
            print(f"{api.name}: ERROR - {type(e).__name__}: {e}")
```

Check for:
- Are API keys configured and valid?
- Are rate limits being respected? (MusicBrainz: 1 req/sec with User-Agent; Last.fm: 5 req/sec)
- Are timeouts too aggressive? (increase to 10–15 seconds per call)
- Is name URL-encoding correct? (accents, special chars)
- Network/firewall issues on Render hosting?

### Fix 3: Multi-artist credit splitting

When an artist name contains separators, split and search for the primary artist:

```python
SEPARATORS = [", ", " & ", " and ", " feat. ", " ft. ", " feat ", " ft ", " x "]

def extract_primary_artist(credit: str) -> str:
    for sep in SEPARATORS:
        if sep in credit:
            return credit.split(sep)[0].strip()
    return credit
```

- "Roger Eno, Brian Eno" → search for "Roger Eno"
- "Max Richter, Grace Davidson" → search for "Max Richter"
- "A Winged Victory for the Sullen, Adam Wiltzie, Dustin O'Halloran" → search for "A Winged Victory for the Sullen"

### Fix 4: Sanity check for obviously wrong results

If an artist has ≥1,000 Deezer fans AND scores 0 on Platform Presence, Fan Engagement, IRL Presence, and Industry Signals AND is clean on Blocklist — the problem is our data collection, not the artist. Override:

```python
if (deezer_fans >= 1000 and
    platform_score == 0 and fan_score == 0 and
    irl_score == 0 and industry_score == 0 and
    blocklist_score == 100):
    verdict = "Inconclusive"
    confidence = "low"
    description = f"API errors prevented full analysis of {name}. Deezer data suggests legitimate artist."
```

### Fix 5: Implement name matching instructions

The `name_matching.md` document (already created in a prior session) covers Unicode normalization, fuzzy matching, alias checking, MusicBrainz alias lookups, length-adjusted confidence thresholds, and collaboration splitting. If these haven't been implemented, they need to be now.

### Fix 6: Add API health monitoring

Add a diagnostic section to each report (can be collapsed/hidden) showing actual API status per artist:

```
Artist: Caterina Barbieri
  Deezer:      ✓ Found (200 OK, 0.3s)
  MusicBrainz: ⚠ Timeout (after 5s)
  Last.fm:     ⚠ Error (429 Rate Limited)
  Genius:      ⚠ Error (Connection refused)
  Discogs:     ⚠ Timeout (after 5s)
  Setlist.fm:  ⚠ Timeout (after 5s)
  Wikipedia:   ⚠ Error (403 Forbidden)
```

This makes failures visible instead of silent.

---

# PART B: Document Alignment

These changes bring existing project documents into alignment with `ui-spec-round2.md`.

## B.1 JSON Schema (`playlist_results_schema.md`)

### Radar chart: 8 → 6 dimensions

**Current (wrong):**
```json
"radar": {
  "labels": ["Web Presence", "Streaming Pattern", "Catalog Behavior",
    "Label Intel", "Cross-Platform", "Social Footprint", "Live History", "Credit Network"],
  "scores": [8, 12, 3, 5, 10, 2, 0, 4]
}
```

**Correct:**
```json
"radar": {
  "labels": ["Platform Presence", "Fan Engagement", "Creative History",
    "IRL Presence", "Industry Signals", "Blocklist Status"],
  "scores": [57, 10, 0, 0, 0, 70]
}
```

### Rename "Authentic" → "Likely Authentic"

Find and replace in all verdict enums, `summary.verdict_breakdown` keys, and example JSON. The full enum:
```
"Verified Artist" | "Likely Authentic" | "Inconclusive" | "Suspicious" | "Likely Artificial"
```

### Score ranges (canonical, contiguous)

| Verdict | Range |
|---|---|
| Verified Artist | 82–100 |
| Likely Authentic | 58–81 |
| Inconclusive | 38–57 |
| Suspicious | 18–37 |
| Likely Artificial | 0–17 |

Previous versions used 80–100 / 55–79 / 35–54 / 15–34 / 0–14. The ranges above are canonical.

### Remove `api_usage` from top-level schema

The UI no longer displays API usage. Remove from the JSON the frontend loads. Can still be logged server-side.

### Add new summary fields

```json
"summary": {
  "analyzed_count": 69,
  "timed_out_count": 53,
  "total_playlist_artists": 122,
  "verdict_breakdown": {
    "Verified Artist": 26,
    "Likely Authentic": 1,
    "Inconclusive": 41,
    "Suspicious": 1,
    "Likely Artificial": 0,
    "Not Scanned": 53
  }
}
```

### Add per-artist fields for UI requirements

```json
{
  "profile_urls": {
    "deezer": "https://www.deezer.com/artist/12345",
    "musicbrainz": "https://musicbrainz.org/artist/abc-123",
    "genius": "https://genius.com/artists/Example",
    "lastfm": "https://www.last.fm/music/Example",
    "discogs": "https://www.discogs.com/artist/67890",
    "setlistfm": "https://www.setlist.fm/setlists/abc-123.html",
    "wikipedia": "https://en.wikipedia.org/wiki/Example",
    "bandcamp": "https://example.bandcamp.com",
    "youtube": "https://youtube.com/channel/xyz",
    "official_website": "https://example.com",
    "social": {
      "instagram": "https://instagram.com/example",
      "twitter": "https://twitter.com/example",
      "facebook": "https://facebook.com/example"
    }
  },
  "bio_data": {
    "sources": ["wikipedia", "discogs", "genius"],
    "total_chars": 2450,
    "has_verifiable_details": true,
    "details_found": ["location", "career_timeline", "collaborators"],
    "real_name": "John Smith",
    "is_generic": false
  },
  "musicbrainz_relationship_count": 47,
  "deezer_track_ranks": [
    { "title": "Track A", "rank": 850000 },
    { "title": "Track B", "rank": 120000 }
  ],
  "api_status": {
    "deezer": "found",
    "musicbrainz": "timeout",
    "genius": "error",
    "lastfm": "not_found",
    "discogs": "found",
    "setlistfm": "not_found",
    "wikipedia": "found"
  }
}
```

Note the addition of `api_status` per artist — this supports Fix 1 (separating "not found" from "error") and Fix 6 (API health monitoring).

### Update component mapping table

Remove:
- ~~Dashboard Health Score → `summary.health_score`~~
- ~~API Usage Panel → `api_usage[]`~~

Add:
- Analyzed Count → `summary.analyzed_count`
- Timed Out Count → `summary.timed_out_count`
- Verdict Breakdown Bar → `summary.verdict_breakdown` (includes "Not Scanned" segment)
- API Health (per artist) → `artists[].api_status`

## B.2 Decision Tree (`simplified_scoring_architecture.md`)

### Rename "Authentic" → "Likely Authentic"

Find and replace. Specifically:
- Rule 6 output: "THEN: LIKELY AUTHENTIC (medium confidence)"
- Rule 9 output: "THEN: LIKELY AUTHENTIC (low confidence)"

### Fix score ranges

**Current (wrong):** 82–100 / 58–80 / 38–56 / 18–36 / 0–16
**Correct:** 82–100 / 58–81 / 38–57 / 18–37 / 0–17

Ranges must be contiguous with no gaps.

### Update evidence collector → category mapping

| Category | Evidence Sources |
|---|---|
| Platform Presence | Deezer lookup, YouTube lookup, Wikipedia lookup, Bandcamp (MB urls), social media (MB url-rels), Genius profile, bio analysis |
| Fan Engagement | Last.fm listeners/scrobbles, Deezer fan count, play/listener ratio |
| Creative History | Deezer catalog (albums/singles/duration/concentration), Genius song count, MB artist-rels (collaborators, groups), same-day release detection |
| IRL Presence | Setlist.fm concerts, Discogs physical releases |
| Industry Signals | MB entry + completeness, ISNI/IPI, ASCAP/BMI (when live) |
| Blocklist Status | known_ai_artists.json, pfc_distributors.json, pfc_songwriters.json, entity DB, ISRC registrant matching |

---

# PART C: Enhancements

## C.1 Show Matched Decision Tree Rule in UI

When the decision tree assigns a verdict, the UI should show which rule matched. Add to the expanded artist detail, between the verdict description and the category sections:

> **Flagged by:** Rule 2 — PFC Label + Content Farm Pattern (high confidence)

Or for clean artists:

> **Matched:** Rule 4 — Multi-platform with strong green signals (high confidence)

**Display format:** `[icon] Flagged by Rule {N}: {rule_label} ({confidence} confidence)`

Style subtly — smaller font, muted color. Use verdict-colored tint.

**Rule labels:**

| Rule | Label |
|---|---|
| 1 | Known AI Artist (blocklist match) |
| 2 | PFC Label + Content Farm Pattern |
| 3 | Overwhelming red flags (3+ strong, no green) |
| 4 | Multi-platform with strong green signals |
| 5 | High fan count, multi-platform, no red flags |
| 6 | Green signals outweigh red 2:1 |
| 7 | Red signals outweigh green 2:1 |
| 8 | PFC label (standalone) |
| 9 | More green than red |
| 10 | More red than green |
| 11 | Default — Inconclusive |

**JSON:** Ensure `verdict.matched_rule` contains both number and label:
```json
"verdict": {
  "result": "Likely Artificial",
  "confidence": "high",
  "score": 6,
  "matched_rule": "Rule 2: PFC Label + Content Farm Pattern",
  "threat_category": "1",
  "threat_label": "PFC Ghost Artist"
}
```

## C.2 Bio Analysis — Phased Implementation

### Phase 1 (ship now): Presence + length + generic detection

| Signal | Points | How to implement |
|---|---|---|
| Bio exists on any platform | +15 | Check `bio_chars > 0` on Wikipedia, Discogs, Genius, MusicBrainz |
| Bio on 3+ platforms | +15 bonus | Count platforms with `bio_chars > 0` |
| Bio is generic/boilerplate | -5 | Flag if < 200 chars AND no proper nouns |
| Real name known | +15 | MB `name` vs `sort-name`, or Discogs `realname` |
| No bio on any platform | -30 | All sources empty |

**Bullets for Phase 1:**
- "Biographical info on {N} platforms: {Wikipedia}, {Discogs}, {Genius}" — clickable
- "Real name: {real_name}"
- "No biographical information found on any platform"
- "Bio is brief/generic ({char_count} chars)"

No NLP needed — just string length + basic regex for year detection.

### Phase 2 (backlog): Verifiable detail extraction

| Signal | Points | Implementation |
|---|---|---|
| Bio contains verifiable location | +5 | Entity extraction or keyword matching |
| Bio contains career timeline/years | +5 | Regex for year patterns |
| Bio references specific labels/releases | +5 | Match against known label/album names |
| Bio names specific collaborators | +5 | Cross-ref against MB artist relations |
| Bio has ≥ 3 verifiable detail types | +15 | Count of above |

**Recommended implementation:** Claude Haiku API call per artist bio (~$0.001/artist). Send bio text with structured extraction prompt. Most accurate with least code.

## C.3 ASCAP/BMI Scoring (when live)

Add to Industry Signals scoring table:

| Signal | Condition | Points | Strength |
|---|---|---|---|
| ASCAP/BMI: registered as songwriter | found with ownership | +30 | Strong |
| ASCAP/BMI: normal writer/publisher split | ~50/50 | +5 | Weak |
| ASCAP/BMI: 0% songwriter share | 100% publisher | -15 | Moderate |
| ASCAP/BMI: publisher matches PFC entity | blocklist match | -30 | Strong |
| Not found in ASCAP or BMI | no registration | -5 | Weak |

"Not found" is intentionally weak (-5) — many legit non-US/indie artists aren't registered. "Found with ownership" is strong (+30). Same asymmetric pattern as ISNI/IPI.

PFC publisher match also emits a Blocklist Status hit (cross-category signal).

**Bullets:**
- "Registered with {ASCAP|BMI}: {N} works, songwriter holds {X}% share" — strong green
- "Registered but publisher '{name}' holds 100% (0% songwriter share)" — moderate red
- "Publisher '{name}' matches PFC entity database" — strong red
- "Not found in ASCAP or BMI" — in consolidated negative line

**Conditional execution:** Only scrape for artists with ≥ 1 moderate red flag.

## C.4 Deezer AI Detection Scoring (when live)

Add to decision tree as Rule 1.5 (between Known AI Artist and PFC Label + Content Farm):

```
RULE 1.5: Deezer AI Content Flag
  IF: any evidence has tag "ai_generated_music" from source "Deezer AI Detection"
  THEN: LIKELY ARTIFICIAL (high confidence)
```

Add to Blocklist Status scoring:

| Signal | Condition | Deduction | Strength |
|---|---|---|---|
| Deezer AI content flag | detected | -100 (→ 0) | Strong |

**Bullet:** "Flagged as AI-generated content by Deezer's detection system" — strong red

Check for all artists (free API field, not a scrape).

## C.5 Export & Share

Users need to get report data out of the app — for building a case, sharing with colleagues, or archiving evidence.

### Shareable report URL

Each report already has a URL (`/report/{id}`). Ensure these are:
- Stable (don't expire or require auth)
- Bookmarkable
- Open Graph tagged for social previews: title = "Playlist Audit: {name}", description = "{N} artists analyzed, {N} flagged", image = auto-generated summary card

### CSV export of flagged artists

Add an "Export CSV" button to the report header. Include one row per artist with columns:

```
artist_name, spotify_url, verdict, score, confidence, threat_category,
platform_presence_score, fan_engagement_score, creative_history_score,
irl_presence_score, industry_signals_score, blocklist_score,
matched_rule, deezer_fans, lastfm_listeners, album_count, single_count,
top_red_flags (semicolon-separated)
```

This is the format most useful for someone compiling evidence or doing their own analysis in a spreadsheet.

### Copy summary to clipboard

Add a "Copy Summary" button that copies a plain-text block:

```
Playlist Audit: Lava Lamp
Analyzed: 35 of 96 artists (61 timed out)
Verified: 3 | Likely Authentic: 9 | Inconclusive: 0 | Suspicious: 23 | Likely Artificial: 0
Threat breakdown: 23 PFC Ghost Artist
Flagged artists: Caterina Barbieri (23), Slow Meadow (23), Susumu Yokota (23)...
Report: https://spotify-auditor.../report/a54043da
```

## C.6 Playlist History & Comparison

Over time you'll scan the same playlist multiple times. Track changes.

### Store summary snapshots

Every time a playlist is scanned, persist a timestamped summary:

```json
{
  "playlist_id": "37i9dQZF1DX...",
  "scanned_at": "2026-02-27T14:30:00Z",
  "analyzed_count": 35,
  "verdict_breakdown": { ... },
  "flagged_count": 23,
  "flagged_artists": ["Caterina Barbieri", "Slow Meadow", ...]
}
```

### Trend display (future)

When a playlist has ≥ 2 scans, show a small trend line on the report header:
- "Previously scanned Jan 15: 18 flagged → now 23 flagged (+5)"
- Sparkline of flagged count over time

This is powerful for tracking whether Spotify is adding more PFC content to a playlist over time. Not needed for v1, but design the data model now so it's easy to add later.

### Diff view (future)

Show which artists were added/removed between scans and how verdicts changed. Low priority but extremely useful for investigative work.

## C.7 Control Group Baseline

The report shows "23 flagged out of 35" but the reader has no context for whether that's normal. Add a static benchmark.

### Static baseline (ship now)

Add a line below the verdict bar:

> "Typical editorial playlist: 10–20% flagged | This playlist: 66%"

Based on your existing research — run 5–10 diverse editorial playlists (Today's Top Hits, RapCaviar, Rock Classics, etc.) and compute the average flagged percentage. Use that as the baseline.

### Dynamic baseline (future)

As more playlists are scanned, compute a running average across all scanned playlists. Display as a reference line on the verdict bar:
- Gray dashed line at the average position
- Tooltip: "Average across {N} scanned playlists: {X}% flagged"

### Genre-specific baselines (future)

Ambient/chill playlists may naturally have higher false-positive rates because the artists tend to be more niche. If you accumulate enough scan data, compute baselines per genre cluster rather than one global average.

## C.8 Artist Image Analysis

The earlier architecture docs include `ai_generated_image` and `stock_photo` tags in the evidence vocabulary, but the UI spec doesn't address where to display these or how to score them.

### Scoring (add to Platform Presence)

| Signal | Condition | Points | Strength |
|---|---|---|---|
| Profile image flagged as AI-generated | detected | -15 | Moderate |
| Profile image matches stock photography | detected | -15 | Moderate |
| No profile image | missing | -5 | Weak |

These go in Platform Presence because the profile image is part of the artist's platform identity.

### Display bullet (Platform Presence)

- "Profile image flagged as AI-generated" — moderate red
- "Profile image identified as stock photography" — moderate red
- Omit any bullet if the image passes checks (don't show "Image appears authentic" — that's not useful)

### Implementation note

Image analysis requires either:
1. A Claude Vision API call per artist image (~$0.003/image) — most accurate
2. A reverse image search against stock photo databases — cheaper but less reliable
3. Heuristic checks (image resolution, metadata, EXIF data) — least reliable

Recommend shipping without image analysis in v1. Add as a Phase 2 enhancement. Design the scoring table and bullet now so the frontend is ready when the backend capability exists.

## C.9 Print & Read-Mode Stylesheet

If someone prints a report or presents it on a shared screen, the dark theme with colored badges may not reproduce well.

### Print CSS

Add a `@media print` stylesheet that:
- Switches to white background, black text
- Replaces colored verdict badges with high-contrast text + the accessibility symbols (✓ ○ △ ✗)
- Removes interactive elements (expand/collapse, sort/filter)
- Expands all artist cards
- Renders the radar chart in grayscale with labeled axes
- Adds the report URL as a footer on every page
- Keeps page breaks between artists clean

### Read mode (future)

Add a "Presentation Mode" toggle that:
- Switches to light theme
- Enlarges text
- Hides the sidebar/nav
- Focuses on the verdict bar + flagged artist list

Low priority — trivial to implement as a CSS-only toggle and makes the tool more shareable in professional settings.

---

# PART D: Consolidated Checklist

Everything from the UI spec checklist plus all items from this document.

### Critical Fixes (do first)
- [ ] **Fix 1:** Separate "not found" / "error" / "timeout" / "skipped" in LookupResult
- [ ] **Fix 2:** Run API diagnostic — which APIs are actually working right now?
- [ ] **Fix 3:** Split multi-artist credits, search primary artist name
- [ ] **Fix 4:** Add sanity check — Deezer fans + all zeros = Inconclusive, not Suspicious
- [ ] **Fix 5:** Implement name matching instructions from `name_matching.md`
- [ ] **Fix 6:** Add per-artist API status logging to results JSON

### Backend (evaluation engine)
- [ ] Implement per-category scoring tables per `ui-spec-round2.md` Part 5
- [ ] Blocklist: AI blocklist / PFC label → score 0. Songwriter/distributor → -80.
- [ ] Physical releases (Discogs) scored as strong (+30)
- [ ] Top track concentration: 3 tiers (≥90% / 80–89% / 70–79%)
- [ ] Bio analysis Phase 1: presence + length + generic detection
- [ ] Genius: binary presence (any songs = +5 weak)
- [ ] MusicBrainz completeness: count relationships → rich/moderate/stub
- [ ] Remove: mood-word track titles, touring geography, named tours
- [ ] Evidence deduplication: don't emit raw + summary for same signal
- [ ] Emit `matched_rule` in verdict object
- [ ] Store profile URLs per platform
- [ ] Store bio text for frontend display
- [ ] Store MusicBrainz relationship count
- [ ] Store Deezer per-track rank data

### Frontend (React)
- [ ] Render bullets per ui-spec-round2.md Part 6
- [ ] All platform names clickable links (new tab)
- [ ] Category colors: 4-tier (green/light-green/orange/red) + gray for no-data
- [ ] Blocklist: binary (100 = green, <100 = red)
- [ ] Platform checkmarks: add ⚠ state for error/timeout (Fix 1)
- [ ] Remove: "Found on N platforms", flag counts, scan time, API calls, contamination, Claude AI Deep Dive, Data Sources panel
- [ ] Rename "Authentic" → "Likely Authentic" everywhere
- [ ] Wikipedia: word count (bytes/6) not bytes
- [ ] "No data" → explicit source names
- [ ] Verdict bar: gray "Not Scanned" + nested threat categories
- [ ] Move methodology to /methodology page
- [ ] Summary: analyzed count + timeout warning
- [ ] Deduplicate evidence bullets
- [ ] Collapsed card: score + name + verdict + threat + chevron only
- [ ] Prototype mini 6-segment category bar
- [ ] Sort/filter controls
- [ ] Standardized verdict description templates
- [ ] Accessibility indicators (✓ ○ △ ✗) alongside colors
- [ ] Confidence: visual weight on verdict badge
- [ ] Restore radar chart
- [ ] Global color constants
- [ ] Mobile responsiveness pass
- [ ] Show `verdict.matched_rule` in expanded detail
- [ ] Show ⚠ on collapsed card when analysis is incomplete (API failures)

### Document Alignment
- [ ] `playlist_results_schema.md`: radar 8→6, rename Authentic, fix score ranges, remove api_usage, add profile_urls/bio_data/api_status, update component mapping
- [ ] `simplified_scoring_architecture.md`: rename Authentic, fix score ranges, update collector→category mapping

### Scoring Alignment
- [ ] Verdict ranges: Verified 82–100, Likely Authentic 58–81, Inconclusive 38–57, Suspicious 18–37, Likely Artificial 0–17
- [ ] Category colors: ≥70 green, 40–69 light green, 15–39 orange, 0–14 red
- [ ] Blocklist: any score < 100 = red
- [ ] 0 with no data = gray
- [ ] Error/timeout = gray (not red, not penalized)

### Enhancements (near-term)
- [ ] CSV export button on report header (C.5)
- [ ] Copy Summary to clipboard button (C.5)
- [ ] Open Graph meta tags for shareable report URLs (C.5)
- [ ] Static control group baseline line below verdict bar (C.7)
- [ ] Print stylesheet — light background, high-contrast, accessibility symbols (C.9)

### Enhancements (backlog)
- [ ] Bio analysis Phase 2 — verifiable detail extraction via Claude Haiku (C.2)
- [ ] ASCAP/BMI integration — scoring per C.3
- [ ] Deezer AI detection — scoring per C.4
- [ ] Playlist history — persist timestamped summary snapshots per playlist (C.6)
- [ ] Playlist trend display — show change over time when ≥ 2 scans exist (C.6)
- [ ] Playlist diff view — added/removed artists between scans (C.6)
- [ ] Dynamic baseline — running average across all scanned playlists (C.7)
- [ ] Genre-specific baselines (C.7)
- [ ] Artist image analysis — AI/stock detection, scoring in Platform Presence (C.8)
- [ ] Presentation/read mode toggle (C.9)
- [ ] Reduce timeout rate (retry logic, parallelization, bottleneck tracking)
