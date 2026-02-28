# Demo Report Audit — Claude Code Task List

**Audited URL:** `https://spotify-auditor-1234556778.onrender.com/demo`  
**Date:** 2026-02-27  
**Canonical specs:** `ui-spec-round2.md` (Part refs below), `fixes-alignment-enhancements.md`

Each issue includes: what's wrong, where to find it in the codebase, what the spec says, and exactly what to change. Issues are grouped by layer (backend evidence generation → backend scoring → frontend rendering → frontend layout) so Claude Code can work through one layer at a time.

---

## LAYER 1: BACKEND — Evidence Generation

These issues originate where evidence bullets are emitted (likely in evidence collector functions or the evaluation/decision-tree module).

---

### BUG-01: Blocklist Status emits 3–4 duplicate "clean" bullets

**Observed on every artist with Blocklist Status = 100:**
```
✓ Clean across all blocklists
✓ Clean across all blocklists          ← duplicate
✓ Not matched on any blocklists        ← duplicate (reworded)
✓ No prior intelligence in entity database  ← duplicate (reworded)
```

**Spec (Part 6.6):** When clean, emit exactly ONE bullet: `"Clean across all blocklists"`. No other green lines.

**Fix:** Find the Blocklist Status evidence emitter. It likely has separate emit calls for:
1. General blocklist check (known_ai_artists.json)
2. PFC distributor check (pfc_distributors.json)
3. PFC songwriter check (pfc_songwriters.json)
4. Entity database check

Each of these is emitting its own "clean" bullet. **Replace** with a single consolidated emit:
```python
# BEFORE (approximate — find the actual pattern):
if not matched_ai_blocklist:
    emit("green", "Clean across all blocklists")
if not matched_pfc_distributors:
    emit("green", "Not matched on any blocklists")
if not matched_entity_db:
    emit("green", "No prior intelligence in entity database")

# AFTER:
any_blocklist_hit = matched_ai_blocklist or matched_pfc_distributors or matched_pfc_songwriters or matched_entity_db
if not any_blocklist_hit:
    emit("green", "Clean across all blocklists")  # single bullet
```

---

### BUG-02: Fan Engagement emits duplicate Last.fm data

**Observed on nearly every artist:**
```
✓ Found on Last.fm (8,851 listeners, 24,785 scrobbles)
✓ Last.fm: 8,851 listeners          ← same data, restated
```

**Spec (Part 6.2):** Each data point renders exactly once. The canonical bullet for Last.fm is:
`"{scrobble_count} Last.fm scrobbles ({listener_count} listeners, {ratio}x play ratio)"` — link to Last.fm profile.

**Fix:** Find the Fan Engagement evidence emitter. Remove the secondary `"Last.fm: X listeners"` bullet. Keep only the full line that includes both listeners and scrobbles. If the play/listener ratio is already emitted as a separate bullet (e.g., "Moderate scrobble engagement (play/listener ratio: 4.8)"), fold the ratio into the main Last.fm line instead:
```
# Emit ONE bullet:
"24,785 Last.fm scrobbles (8,851 listeners, 2.8x play ratio)"

# Do NOT also emit:
# "Found on Last.fm (8,851 listeners, 24,785 scrobbles)"
# "Last.fm: 8,851 listeners"
# "Moderate scrobble engagement (play/listener ratio: 2.8)"
```

---

### BUG-03: "Average track length: 0 seconds" emitted as red flag when data is missing

**Observed on:** Erik Moreau, Lara Di Umbra, Peqasus, Suuunday, and 10+ others.
```
✗ Average track length: 0 seconds
```

**Root cause:** The Deezer catalog data either wasn't fetched or returned no track duration info. A duration of 0 is not real data — no song is 0 seconds long.

**Spec (fixes-alignment-enhancements.md, Fix 1):** Separate `"not_found"` from `"error"` from `"timeout"`. When data is missing/zero due to API failure, emit as **neutral/gray**, not red.

**Fix:** In the Creative History evidence emitter, add a guard:
```python
# BEFORE:
if avg_track_length < 90:
    emit("red", f"Average track length: {avg_track_length} seconds")

# AFTER:
if avg_track_length == 0 or avg_track_length is None:
    pass  # No data — don't emit anything. This is not a signal.
elif avg_track_length < 90:
    emit("red", f"Average track length: {avg_track_length} seconds")
```

Apply the same guard to the related "Very uniform track lengths (stdev: 0.0s)" bullet. If avg duration is 0, stdev is meaningless — suppress it.

---

### BUG-04: "Found on N platforms" meta-count bullet still emitted

**Observed on every artist in Platform Presence:**
```
✓ Found on 2 platforms
```

**Spec (Part 7, rule 5):** "No redundant counts. Don't show 'Found on N platforms' or 'N green / N red flags.'" The individual platform bullets already convey this.

**Fix:** Find and remove the emit call that produces `"Found on N platforms"` in the Platform Presence evidence emitter.

---

### BUG-05: Physical releases (Discogs) emitted in BOTH Creative History and IRL Presence

**Observed on:** Marc & Friends, Trois Amis, Solmer, Leo Button, The Lone Winter.

Creative History shows: `✓ 2 physical releases (Discogs)`  
IRL Presence shows: `✓ 2 physical release(s) on Discogs` and `✓ 2 vinyl/CD (Discogs)`

**Spec (Part 5.4, evidence collector mapping):** Discogs physical releases belong to **IRL Presence only**. Creative History uses Deezer catalog, Genius, and MusicBrainz data.

**Fix:**
1. Remove all Discogs physical release bullets from the Creative History emitter.
2. In IRL Presence, consolidate to ONE bullet: `"{N} physical releases on Discogs (vinyl/CD)"` — link to Discogs profile.
3. Do NOT emit both `"2 physical release(s) on Discogs"` and `"2 vinyl/CD (Discogs)"` — these are the same data.

---

### BUG-06: Industry Signals emits Discogs bio data (belongs in Platform Presence)

**Observed on:** Pireas, Leo Button, The Lone Winter.
```
Industry Signals:
  ✓ Detailed Discogs bio with career history (238 chars)
  ✓ Detailed Discogs bio          ← duplicate
```

**Two issues:**
1. Bio analysis is a Platform Presence signal per the spec (Part 5.1), not Industry Signals.
2. The bio bullet is duplicated (full version + short version).

**Fix:**
1. Move bio evidence emission from Industry Signals emitter to Platform Presence emitter.
2. Emit ONE bullet: `"Discogs bio ({char_count} chars, career details found)"` or similar.

---

### BUG-07: YouTube status emitted as neutral dot (•) instead of ✓/✗/⚠

**Observed on every artist:** `• YouTube` in the platform checkmarks row.

**Spec (Part 3, fixes-alignment-enhancements Fix 1):** Platform checkmarks must be one of:
- `✓` — found (clickable link to profile)
- `✗` — not found (API returned no match)
- `⚠` — error/timeout (API failed)

A bare `•` is not a valid state.

**Fix:** In the platform checkmark renderer and/or the evidence data, ensure YouTube lookup results include a proper status. If YouTube data collection isn't implemented yet, emit `✗ YouTube` (not found). If it was attempted but failed, emit `⚠ YouTube`.

---

## LAYER 2: BACKEND — Scoring Logic

These issues affect the numerical scores assigned to categories.

---

### BUG-08: Platform Presence scores 0 even when artist is found on 2–3 platforms

**Observed on:** Nearly every artist. Example — Meladonica found on MusicBrainz + Last.fm → Platform Presence: 0.

**Spec (Part 5.1):** Being found on MusicBrainz should contribute points. Being found on Last.fm (for the purposes of Platform Presence — separate from Fan Engagement) is also a platform presence signal. Found on Genius = +5 weak. Found on Deezer = green if ≥10K fans.

**Root cause (likely):** The Platform Presence scoring function may only be counting Deezer, YouTube, Wikipedia, and social media — not MusicBrainz/Last.fm/Discogs presence. Or, "found on Last.fm" is only flowing into the Fan Engagement category and not also counting toward Platform Presence.

**Fix:** Review the Platform Presence scoring function. Per the spec's evidence-to-category mapping:
- Platform Presence sources: Deezer lookup, YouTube lookup, Wikipedia lookup, Bandcamp (MB urls), social media (MB url-rels), Genius profile, bio analysis
- Each found platform should contribute points: found on any platform = at minimum weak green (+5)
- MusicBrainz found → this feeds Industry Signals, not Platform Presence. But having social/web URLs from MusicBrainz DOES feed Platform Presence.

If the issue is that MusicBrainz/Last.fm/Discogs don't count toward Platform Presence per spec, then a score of 0 is actually correct when Deezer/YouTube/Wikipedia/Bandcamp/social/Genius are all not found. In that case, the score of 0 should display as **gray** (no data from relevant sources), not red. See BUG-09.

---

### BUG-09: Category score 0 with no evidence renders as red — should be gray

**Observed on:** Platform Presence: 0, Creative History: 0, IRL Presence: 0, Industry Signals: 0 across many artists when the section shows "No data."

**Spec (Part 4):** "Special case — 0 with no data: Display as gray (`#9ca3af`) not red. Zero data ≠ negative data."

**Fix:** In the frontend category score color function (and/or the backend if it sends a color hint), add logic:

```javascript
function getCategoryColor(score, hasAnyEvidence, categoryName) {
  // Blocklist is always binary
  if (categoryName === 'Blocklist Status') {
    return score === 100 ? '#22c55e' : '#ef4444';
  }
  // No data = gray
  if (score === 0 && !hasAnyEvidence) {
    return '#9ca3af'; // gray
  }
  // 4-tier system
  if (score >= 70) return '#22c55e';  // green
  if (score >= 40) return '#86efac';  // light green
  if (score >= 15) return '#f97316';  // orange
  return '#ef4444';                   // red
}
```

The key is determining `hasAnyEvidence`. If the category section contains ONLY "No data" or zero evidence bullets, it's gray. If it has actual red flags (even with score 0), it's red.

---

### BUG-10: "No PRO registration found" renders as neutral (•) — should be weak red (✗)

**Observed on:** Most artists in Industry Signals.
```
• No PRO registration found
```

**Spec (Part 5.5):** PRO absence = "Not registered with ASCAP, BMI, or SESAC" → weak negative signal (-5).

**Fix:** Change the emit from neutral to weak red:
```python
# BEFORE:
emit("neutral", "No PRO registration found")

# AFTER:
emit("red", "weak", "Not registered with ASCAP, BMI, or SESAC")
```

Note: If ASCAP/BMI integration isn't live yet, this bullet should either be omitted entirely or shown as neutral with a note that the check isn't available yet. Don't penalize artists for a check that hasn't been implemented.

---

## LAYER 3: FRONTEND — Report Summary Section

These issues are in the React component that renders the summary area at the top of the report page.

---

### BUG-11: "Health Score" stat card still visible — should be removed

**Current:** Shows `57 Health Score` with tagline "57% of artists show legitimacy signals."

**Spec (fixes-alignment-enhancements.md, Part B.1):** Remove `summary.health_score` from the schema and remove the Dashboard Health Score component.

**Fix:** In the report summary React component, delete or hide the Health Score card. The summary should show:
- Analyzed count: `"{N} Analyzed"`
- Timed-out count: `"{N} Timed Out ⚠️"` (show even if 0, as `"0 Timed Out ✓"`)
- Flagged count: `"{N} Flagged"` (keep this one)

Remove the Health Score from the JSON data source as well so it doesn't reappear.

---

### BUG-12: Verdict breakdown bar missing "Not Scanned" gray segment

**Current:** Bar shows 42 + 3 + 35 = 80 total. No gray segment for any unscanned/timed-out artists.

**Spec (Part 1):** Add a gray `#9ca3af` segment for timed-out artists. The bar should add up to the full playlist size:
`analyzed + timed_out = total_playlist_artists`

**Fix:** In the verdict bar component, add a segment:
```jsx
{summary.timed_out_count > 0 && (
  <div
    style={{
      width: `${(summary.timed_out_count / summary.total_playlist_artists) * 100}%`,
      backgroundColor: '#9ca3af'
    }}
    title={`${summary.timed_out_count} Not Scanned`}
  />
)}
```

If the demo data has 0 timeouts, that's fine — the segment just won't render. But ensure the data schema includes `timed_out_count` and the component handles it.

---

### BUG-13: Threat Categories section not visually nested under verdict bar

**Current:** Threat Categories appears as a standalone section below the verdict bar with no visual connection.

**Spec (Part 1):**
- Position directly below verdict bar, indented/nested
- Label: **"Threat Breakdown"**
- Subtitle: `"{N} artists flagged as Suspicious or Likely Artificial"` (dynamic count)
- Visual connector: bracket, indent, shared background, or connector line linking to the red/orange verdict segments

**Fix:** Wrap the Threat Categories content in a container that:
1. Has `margin-left: 20px` or similar indentation
2. Has a left border or bracket connecting it to the verdict bar above
3. Replaces the heading "Threat Categories" with "Threat Breakdown"
4. Adds the dynamic subtitle line

Example structure:
```jsx
<div className="threat-breakdown" style={{
  marginLeft: '20px',
  borderLeft: '3px solid #f97316',
  paddingLeft: '16px',
  marginTop: '8px'
}}>
  <h3>Threat Breakdown</h3>
  <p className="subtitle">
    {flaggedCount} artists flagged as Suspicious or Likely Artificial
  </p>
  {/* existing threat category pills/bars */}
</div>
```

---

## LAYER 4: FRONTEND — Artist Card (Collapsed State)

---

### BUG-14: Collapsed card shows scrobble counts and physical release mini-stats

**Observed:** Collapsed cards show stat pills like `24,785 Scrobbles` and `2 physical Releases`.

**Spec (Part 2):** Collapsed card shows ONLY:
1. Score badge (number + verdict color)
2. Artist name (full, never truncated)
3. Verdict tag ("Suspicious", etc.)
4. Threat category tag (if applicable)
5. Expand chevron

Everything else (scrobbles, physical releases, Deezer fans, platform checkmarks) moves to expanded body only.

**Fix:** In the collapsed card component, remove/hide the mini stat pills. They should only render inside the expanded `<details>` or equivalent.

---

### BUG-15: Filter controls only show "All | Flagged" — spec requires per-verdict filters

**Current:** Two filter buttons: `All`, `Flagged`.

**Spec (Part 2):** Filter buttons for each verdict:
`All | Verified | Likely Authentic | Inconclusive | Suspicious | Likely Artificial`

**Fix:** Replace the `Flagged` button with individual verdict buttons. Each filters the artist list to show only artists with that verdict. Use the spec's verdict colors for each button's active state.

---

### BUG-16: Confidence level not visually differentiated on score badges

**Current:** All score badges look identical regardless of confidence (low/medium/high).

**Spec (Part 2):**
- **High confidence:** solid badge, full opacity
- **Medium confidence:** standard badge (default styling)
- **Low confidence:** outlined/ghost badge, dashed border or reduced opacity

**Fix:** In the score badge component, read the `confidence` field and apply a CSS class:
```css
.badge-high { opacity: 1; border: 2px solid; background: var(--verdict-color); }
.badge-medium { opacity: 1; border: 1px solid; background: var(--verdict-color); }
.badge-low { opacity: 0.7; border: 1px dashed; background: transparent; color: var(--verdict-color); }
```

---

## LAYER 5: FRONTEND — Artist Card (Expanded State)

---

### BUG-17: Platform checkmarks are not all clickable links

**Current:** Only Last.fm shows as a link. MusicBrainz ✓, Discogs ✓ are plain text.

**Spec (Part 3):** Each ✓ platform must be a clickable `<a>` tag linking to the artist's actual profile on that platform, opening in a new tab. ✗ platforms are not clickable.

**Fix:** The backend needs to include `profile_urls` in the artist JSON (per fixes-alignment-enhancements.md schema). The frontend checkmark component should render:
```jsx
{artist.profile_urls?.musicbrainz ? (
  <a href={artist.profile_urls.musicbrainz} target="_blank" rel="noopener">
    ✓ MusicBrainz
  </a>
) : (
  <span>✗ MusicBrainz</span>
)}
```

URL patterns per spec (Part 3):
- Deezer → `https://www.deezer.com/artist/{deezer_id}`
- MusicBrainz → `https://musicbrainz.org/artist/{mbid}`
- Genius → Genius artist URL from API response
- Last.fm → `https://www.last.fm/music/{artist_name_encoded}`
- Discogs → `https://www.discogs.com/artist/{discogs_id}`
- Setlist.fm → `https://www.setlist.fm/setlists/{setlistfm_mbid}.html`
- Wikipedia → Wikipedia article URL from API response

---

### BUG-18: Radar chart missing from expanded artist cards

**Current:** Expanded cards show category score bars but no radar chart.

**Spec (Part 3):** Restore the 6-axis radar chart. Desktop: render left of category bars. Mobile: stacked above. Fill polygon uses the verdict color.

**Fix:** A radar chart SVG component already exists in the codebase (see `spotify-audit-output.jsx` in project knowledge — the `RadarChart` component). Integrate it into the expanded card layout:
```jsx
<div className="expanded-content" style={{ display: 'flex', gap: '24px' }}>
  <RadarChart
    data={[
      artist.categories.platform_presence,
      artist.categories.fan_engagement,
      artist.categories.creative_history,
      artist.categories.irl_presence,
      artist.categories.industry_signals,
      artist.categories.blocklist_status
    ]}
    labels={['Platform', 'Fans', 'Creative', 'IRL', 'Industry', 'Blocklist']}
    color={verdictColorMap[artist.verdict]}
    size={220}
  />
  <div className="category-bars">
    {/* existing category score bars */}
  </div>
</div>
```

On mobile (< 768px), switch to `flex-direction: column`.

---

### BUG-19: "No data" text shown without naming specific sources checked

**Observed on:** Multiple artists — Creative History: 0 shows just "No data."

**Spec (Part 7, rule 4):** "Never show 'No data.' Always name the specific source that was checked and came back empty."

**Fix:** Replace `"No data"` with the consolidated negative bullet per category:
- Creative History: `"✗ No catalog data from Deezer · No songs on Genius · No collaborator data from MusicBrainz"`
- IRL Presence: `"✗ No concerts on Setlist.fm · No releases on Discogs"`
- Industry Signals: `"✗ No MusicBrainz entry · No ISNI/IPI codes"`

In the frontend renderer, if a category has zero evidence bullets, generate the appropriate negative-source line instead of "No data."

---

### BUG-20: Category header icons use emoji — add accessibility indicators

**Current:** Categories use emoji: 🌐 👥 🎵 🏢 🎭 🛡

**Spec (Part 4):** Add secondary visual indicators alongside color for accessibility:
- Score ≥ 70 (green): ✓
- Score 40–69 (light green): ○
- Score 15–39 (orange): △
- Score 0–14 (red): ✗

**Fix:** The emoji for category headers can stay as decoration. But the **score value display** next to each category needs the accessibility symbol. For example:
```
🌐 Platform Presence  ✗ 0     (red/gray, with ✗ indicator)
👥 Fan Engagement     △ 30    (orange, with △ indicator)
🛡 Blocklist Status   ✓ 100   (green, with ✓ indicator)
```

---

### BUG-21: Zach Flash — Creative History shows 15 but evidence says "No data"

**Observed:** Creative History score = 15, but the evidence section body says "No data."

**Root cause:** The scoring function assigned 15 points (likely from some evidence like a collaborator or catalog metric) but the evidence emitter didn't produce a corresponding bullet.

**Fix:** This is a backend alignment issue. Ensure every scoring contribution has a matching evidence bullet. If the score is non-zero, there must be at least one evidence bullet explaining why. Add logging or an assertion:
```python
if category_score > 0 and len(evidence_bullets) == 0:
    log.warning(f"Score {category_score} for {category} but no evidence emitted for {artist_name}")
```

---

## LAYER 6: FRONTEND — Summary + Layout

---

### BUG-22: Deezer returns ✗ / 0 fans for every single artist

**Observed on all 80 artists:** `✗ Deezer` in checkmarks, `✗ Deezer: 0 fans` in Fan Engagement.

**Possible causes:**
1. The demo cache (`data/demo/cached_report.json`) was captured during a Deezer API outage
2. The Deezer API key is invalid or rate-limited on the deployment
3. The demo playlist is synthetic and these artists don't exist on Deezer

**Fix:** This is a data issue, not a UI issue. Options:
- Re-run the analysis pipeline for this playlist and re-cache when Deezer is working
- Check Deezer API health: `curl "https://api.deezer.com/search/artist?q=Meladonica"` — if it returns results, the issue is in the pipeline
- For demo purposes, consider using a cached report from a playlist where Deezer data was successfully captured

---

### BUG-23: Methodology link — verify it routes to a dedicated page

**Current:** Shows `"Analyzed across 6 evidence categories using 7 data sources"` but unclear if there's a clickable link to /methodology.

**Spec (Part 1):** The text should be: `"Analyzed across 6 evidence categories using 7 data sources — How does this work? ↗"` where "How does this work? ↗" is a link to `/methodology` (opens in new tab).

**Fix:** Ensure the methodology text includes the link:
```jsx
<p>
  Analyzed across 6 evidence categories using 7 data sources
  {' — '}
  <a href="/methodology" target="_blank" rel="noopener">
    How does this work? ↗
  </a>
</p>
```

And ensure a `/methodology` route exists that renders the full explanation (6 category panels, verdict table, threat category table).

---

## DEFERRED (Not Blocking, Lower Priority)

These items are noted in the spec as "prototype first" or "backlog."

| ID | Item | Spec Ref |
|----|------|----------|
| DEF-01 | Mini 6-segment category bar on collapsed cards | Part 2 ("prototype first") |
| DEF-02 | `matched_rule` display in expanded detail | C.1 |
| DEF-03 | CSV export button | C.5 |
| DEF-04 | Copy Summary to clipboard | C.5 |
| DEF-05 | Open Graph meta tags for shareable URLs | C.5 |
| DEF-06 | Print stylesheet | C.9 |

---

## Execution Order (Recommended)

Work through in this order to avoid rework:

1. **LAYER 1 (Backend evidence):** BUG-01 through BUG-07. These fix the data before it reaches scoring or rendering.
2. **LAYER 2 (Backend scoring):** BUG-08 through BUG-10. These fix the numbers.
3. **Re-cache demo data** after layers 1–2 are deployed. The current `cached_report.json` has the old (buggy) evidence and scores baked in. Run the pipeline on the demo playlist and save a fresh snapshot.
4. **LAYER 3 (Summary):** BUG-11 through BUG-13.
5. **LAYER 4 (Collapsed card):** BUG-14 through BUG-16.
6. **LAYER 5 (Expanded card):** BUG-17 through BUG-21.
7. **LAYER 6 (Data + misc):** BUG-22 through BUG-23.

---

## Global Constants Reference (for all layers)

Use these everywhere. Define once in a shared constants file.

```javascript
// Verdict colors
const VERDICT_COLORS = {
  'Verified Artist':   '#22c55e',
  'Likely Authentic':  '#86efac',
  'Inconclusive':      '#fbbf24',
  'Suspicious':        '#f97316',
  'Likely Artificial': '#ef4444',
  'Not Scanned':       '#9ca3af',
};

// Category score colors (4-tier)
function getCategoryScoreColor(score, hasEvidence, isBlocklist) {
  if (isBlocklist) return score === 100 ? '#22c55e' : '#ef4444';
  if (score === 0 && !hasEvidence) return '#9ca3af'; // gray — no data
  if (score >= 70) return '#22c55e';  // green
  if (score >= 40) return '#86efac';  // light green
  if (score >= 15) return '#f97316';  // orange
  return '#ef4444';                   // red
}

// Verdict score ranges
const VERDICT_RANGES = {
  'Verified Artist':   [82, 100],
  'Likely Authentic':  [58, 81],
  'Inconclusive':      [38, 57],
  'Suspicious':        [18, 37],
  'Likely Artificial': [0, 17],
};

// Accessibility indicators per score tier
const SCORE_INDICATORS = {
  green:      '✓',
  lightGreen: '○',
  orange:     '△',
  red:        '✗',
  gray:       '—',
};
```
