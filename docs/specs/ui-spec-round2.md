# Spotify Playlist Auditor — UI Spec (Round 2)

**Report reference:** `/report/c7313d0b` (chill lofi study beats)
**Date:** 2026-02-27

This is the single source of truth for how the report UI should look and behave. It covers the report summary, artist cards (collapsed and expanded), scoring system, evidence bullets per category, and UX improvements. All changes described here should be implemented end-to-end across backend scoring, evidence generation, and frontend rendering.

---

## Part 1: Report Summary

### Remove from summary
- **"Claude AI Deep Dive"** button/section — remove entirely, add back later
- **Scan time** ("5m 18s") and **API calls** ("483") stat cards — internal metrics, not user-facing
- **Contamination rate** ("34%") stat card — unclear what it measures, not useful
- **Data Sources panel** ("Deezer (69) MusicBrainz (69)...") — developer debug info, remove entirely

### Change in summary
- **Artist count**: Change from "122 Artists" to **"69 Analyzed"** — show only the count that was actually scanned. Drop total playlist artist count.
- **Rename "Authentic"** → **"Likely Authentic"** everywhere (summary bar, artist cards, verdict legend, methodology)
- **Add timeout warning**: Show "53 timed out ⚠️" prominently near the analyzed count. Users need to know 43% of the playlist wasn't scanned.

### Verdict breakdown bar
- Add a **gray "Not Scanned" segment** for timed-out artists so the bar adds up to the full playlist size (69 analyzed + 53 skipped = 122)
- **Nest Threat Categories directly below** the verdict bar with a visual connector to the Suspicious + Artificial segments. Label it **"Threat Breakdown"** with subtitle: _"42 artists flagged as Suspicious or Likely Artificial"_ (dynamic counts). The reader should instantly see that threat categories are a drill-down into the red/orange portion, not a separate analysis.

### Verdict colors
Use a cohesive gradient palette. Define these once as global constants and use everywhere:

| Verdict | Hex | Also Used For |
|---|---|---|
| **Verified Artist** | `#22c55e` | Category score ≥ 70 |
| **Likely Authentic** | `#86efac` | Category score 40–69 |
| **Inconclusive** | `#fbbf24` | — |
| **Suspicious** | `#f97316` | Category score 15–39 |
| **Likely Artificial** | `#ef4444` | Category score 0–14 |
| **Not Scanned / No Data** | `#9ca3af` | Skipped artists, categories with no data |
| **Blocklist Hit** | `#ef4444` | Any blocklist score < 100 (binary red) |

### Methodology section
Move the full "How This Works" explanation (6 evidence category panels, verdict table, threat category table) to a **separate /methodology page** that opens in a new tab. Replace in the report with a single line:

> "Analyzed across 6 evidence categories using 7 data sources — [How does this work? ↗]"

---

## Part 2: Artist Card — Collapsed State

The collapsed card should show **only**:
1. Score badge (number + verdict color)
2. Artist name (full, never truncated)
3. Verdict tag ("Suspicious", "Likely Authentic", etc.)
4. Threat category tag (if applicable — only for Suspicious/Likely Artificial)
5. Expand chevron

**Remove from collapsed card**: Deezer fans, Last.fm listeners, Wikipedia indicator, platform checkmarks, blocklist status text. These all move into the expanded body.

### Confidence level visual treatment
Confidence (high/medium/low) should be visible on the collapsed card through the verdict badge styling:
- **High confidence**: solid badge, full opacity
- **Medium confidence**: standard badge
- **Low confidence**: outlined/ghost badge, dashed border or reduced opacity

### Mini 6-segment category bar (prototype first)
A compact horizontal bar (~200px × 8px) showing all 6 category scores as color blocks using the 4-tier system. No numbers — just color. Gives an instant "fingerprint" per artist without expanding.

```
[████████|██|████|░░|░░|████████]
 Platform Fan Creative IRL  Ind  Blocklist
 (green)  (org)(l.grn) (red)(red)(green)
```

**→ Build a prototype in isolation first.** Test with 5–6 sample artists across different verdict levels. Questions to answer before shipping: Does it read well at small sizes? Is it useful on mobile? Does it add value or just noise?

### Verdict description templates
Standardize the one-line description shown below the artist name. Currently inconsistent across artists. Use fixed templates:

- **Verified Artist**: "{name} shows strong evidence of legitimacy across {N} platforms."
- **Likely Authentic**: "{name} appears legitimate. {N} positive and {N} negative signals."
- **Inconclusive**: "Evidence on {name} is mixed — {N} positive and {N} negative signals."
- **Suspicious**: "{name} shows warning signs. Found on {N} platforms with {N} red flags."
- **Likely Artificial**: "{name} has strong indicators of being artificial. {specific top reason — e.g., 'PFC label match' or 'content farm pattern'}."

### Sort and filter controls
Add above the artist list:
- **Sort by**: Score (ascending — worst first, default), Score (descending), Alphabetical
- **Filter by verdict**: All / Verified / Likely Authentic / Inconclusive / Suspicious / Likely Artificial
- Simple toggle buttons, nothing complex

---

## Part 3: Artist Card — Expanded State

When expanded, show in this order:
1. **Platform checkmarks row** — the ✓/✗ indicators for each data source. Each ✓ is a **clickable link** to the artist's actual profile on that platform (new tab). ✗ sources are not clickable.

Platform profile URLs:
- **Deezer** → `https://www.deezer.com/artist/{deezer_id}`
- **MusicBrainz** → `https://musicbrainz.org/artist/{mbid}`
- **Genius** → Genius artist URL from API
- **Last.fm** → `https://www.last.fm/music/{artist_name}`
- **Discogs** → `https://www.discogs.com/artist/{discogs_id}`
- **Setlist.fm** → `https://www.setlist.fm/setlists/{setlistfm_mbid}.html`
- **Wikipedia** → Wikipedia article URL from API

2. **Radar chart** — restore from earlier mockups. 6-axis chart mapping to the 6 categories. Place left of category bars on desktop, stacked above on mobile. Fill polygon uses the verdict color.

3. **6 category sections** — each showing the category name, score (number + color), and evidence bullets as defined below.

---

## Part 4: Scoring System

### Per-Category Scores (0–100)

Each category produces a score from 0–100 where **100 = fully legitimate** and **0 = maximum suspicion**.

**Point values per evidence item:**

| Strength | Green Flag | Red Flag |
|---|---|---|
| Strong | +30 pts | -30 pts |
| Moderate | +15 pts | -15 pts |
| Weak | +5 pts | -5 pts |

**Category score calculation:**
1. Sum all points for the category (green adds, red subtracts)
2. Normalize to 0–100 scale based on the maximum possible score for that category
3. Clamp to 0–100

### Per-Category Color Thresholds (4-tier system)

| Score Range | Color | Hex | Meaning |
|---|---|---|---|
| 70–100 | Green | `#22c55e` | Positive signals dominate |
| 40–69 | Light green | `#86efac` | More positive than negative, mixed |
| 15–39 | Orange | `#f97316` | More negative than positive |
| 0–14 | Red | `#ef4444` | Strong negative signals |

**Special case — 0 with no data:** Display as **gray** (`#9ca3af`) not red. Zero data ≠ negative data.

**Exception — Blocklist:** Binary. 100 = green. Anything below 100 = red. The 4-tier system does not apply to Blocklist.

### Color accessibility
Add secondary visual indicators alongside color so signals are readable without color perception:
- Green: ✓ checkmark
- Light green: ○ open circle
- Orange: △ triangle
- Red: ✗ cross

### Overall Artist Score (0–100)

Determined by the **decision tree** (not a category average). The tree evaluates evidence tags and flag counts to assign a verdict, then places the score within the verdict's range:

| Verdict | Score Range |
|---|---|
| Verified Artist | 82–100 |
| Likely Authentic | 58–81 |
| Inconclusive | 38–57 |
| Suspicious | 18–37 |
| Likely Artificial | 0–17 |

Position within range determined by confidence level and flag balance (see simplified scoring architecture doc).

---

## Part 5: Per-Category Signal → Score Tables

### 5.1 PLATFORM PRESENCE (0–100)

| Signal | Found | Not Found | Strength |
|---|---|---|---|
| Deezer artist exists | +5 | -5 | Weak |
| YouTube channel exists | +5 | -5 | Weak |
| Bandcamp page exists | +5 | 0 | Weak |
| Wikipedia article exists | +15 | -5 | Moderate |
| Wikipedia article ≥ 5,000 words | +15 (bonus) | — | Moderate |
| Official website | +15 | 0 | Moderate |
| Genius profile exists | +5 | -5 | Weak |
| Has ≥ 2 social media links | +15 | 0 | Moderate |
| Has ≥ 4 social media links | +15 (bonus) | — | Moderate |
| Bio exists on any platform | +15 | -15 | Moderate |
| Bio exists on 3+ platforms | +15 (bonus) | — | Moderate |
| Bio contains verifiable location | +5 (bonus) | — | Weak |
| Bio contains career timeline/years | +5 (bonus) | — | Weak |
| Bio references specific labels or releases | +5 (bonus) | — | Weak |
| Bio names specific collaborators | +5 (bonus) | — | Weak |
| Bio has ≥ 3 verifiable detail types | +15 (bonus) | — | Moderate |
| Bio is generic/boilerplate (no specifics) | — | -5 | Weak |
| Real name known | +15 | 0 | Moderate |
| No bio on any platform | — | -30 | Strong |

### 5.2 FAN ENGAGEMENT (0–100)

| Signal | Condition | Points | Strength |
|---|---|---|---|
| Found on Last.fm | yes | +15 | Moderate |
| Last.fm listeners ≥ 10K | yes | +15 | Moderate |
| Last.fm listeners ≥ 100K | yes | +15 (bonus) | Moderate |
| Play/listener ratio 2.0–15.0 | healthy range | +15 | Moderate |
| Play/listener ratio < 2.0 | low repeat | -5 | Weak |
| Play/listener ratio > 15.0 | suspicious | -15 | Moderate |
| Deezer fans > 0 | yes | +5 | Weak |
| Deezer fans ≥ 100 | yes | +15 | Moderate |
| Deezer fans = 0 | with Last.fm > 10K | -15 | Moderate |
| Not found on Last.fm | — | -15 | Moderate |
| Not found on Last.fm AND 0 Deezer fans | — | -30 | Strong |

### 5.3 CREATIVE HISTORY (0–100)

| Signal | Condition | Points | Strength |
|---|---|---|---|
| Has ≥ 1 album | yes | +15 | Moderate |
| Has ≥ 3 albums | yes | +15 (bonus) | Moderate |
| Avg track duration ≥ 180s | full-length | +5 | Weak |
| Avg track duration 90–180s | normal | 0 | — |
| Avg track duration < 90s | stream farm | -30 | Strong |
| Duration stdev < 10s (≥ 5 tracks) | cookie-cutter | -15 | Moderate |
| ≥ 20 singles, 0 albums | content farm pattern | -15 | Moderate |
| ≥ 40 singles, 0 albums | strong content farm | -30 | Strong |
| ≥ 3 collaborators (MB/Genius) | creative network | +15 | Moderate |
| ≥ 10 collaborators | rich network | +15 (bonus) | Moderate |
| Member of group(s) | yes | +15 | Moderate |
| Any songs on Genius | cataloged work | +5 | Weak |
| Top 2 tracks ≥ 90% of Deezer rank | extreme concentration | -30 | Strong |
| Top 2 tracks 80–89% of Deezer rank | high concentration | -15 | Moderate |
| Top 2 tracks 70–79% of Deezer rank | moderate concentration | -5 | Weak |
| Same-day releases ≥ 3 | suspicious | -15 | Moderate |
| Empty catalog | no tracks at all | -30 | Strong |

### 5.4 IRL PRESENCE (0–100)

| Signal | Condition | Points | Strength |
|---|---|---|---|
| ≥ 1 concert on Setlist.fm | any live history | +15 | Moderate |
| ≥ 10 concerts | established touring | +30 | Strong |
| ≥ 1 physical release (Discogs) | tangible evidence | +30 | Strong |
| ≥ 5 physical releases | established catalog | +15 (bonus) | Moderate |
| No concerts on Setlist.fm | — | -15 | Moderate |
| No physical releases on Discogs | — | -15 | Moderate |
| No concerts AND no physical releases | — | -30 | Strong |

### 5.5 INDUSTRY SIGNALS (0–100)

| Signal | Condition | Points | Strength |
|---|---|---|---|
| MusicBrainz entry exists | yes | +5 | Weak |
| MB rich profile (≥ 10 relationships) | yes | +30 | Strong |
| MB moderate profile (3–9 relationships) | yes | +15 | Moderate |
| MB stub (name only, 0–2 relationships) | yes | -5 | Weak |
| MB has type + country + dates | complete metadata | +15 | Moderate |
| ISNI registered | yes | +30 | Strong |
| IPI registered | yes | +30 | Strong |
| ASCAP/BMI registered | yes | +30 | Strong |
| ASCAP/BMI with PFC publisher | PFC match | -30 | Strong |
| No MusicBrainz entry | — | -15 | Moderate |
| No ISNI, IPI, or PRO registration | — | -5 | Weak |

**Note:** Many legit indie artists lack ISNI/IPI/ASCAP. Missing = weak negative. Having = strong positive. Asymmetric by design.

### 5.6 BLOCKLIST STATUS (0–100)

Starts at 100. Deductions per match. Any hit → binary red display.

| Signal | Condition | Deduction | Strength |
|---|---|---|---|
| Artist name on known AI blocklist | exact match | -100 (→ 0) | Strong |
| Label matches PFC distributor list | match | -100 (→ 0) | Strong |
| Songwriter matches PFC songwriter list | match | -80 | Strong |
| Distributor matches PFC entity | match | -80 | Strong |
| ISRC registrant matches PFC distributor | match | -60 | Strong |
| Shares ≥ 3 songwriters with known PFC artists | network match | -50 | Strong |
| Clean across all blocklists | no matches | 100 (no change) | — |

---

## Part 6: Evidence Bullets Per Category

Each category has exactly **one** consolidated negative bullet at the end listing all not-found sources relevant to that section. All platform names with data are **clickable links** to the artist's actual profile (new tab).

### 6.1 PLATFORM PRESENCE — Where the artist exists and who they are

**Platform bullets (show when found):**
- Deezer: "Found on Deezer" — link to `deezer.com/artist/{id}`
- YouTube: "{subscriber_count} YouTube subscribers" — link to channel URL
- Bandcamp: "Has Bandcamp page" — link to Bandcamp URL
- Wikipedia: "Wikipedia article (~{word_count} words, {monthly_views} monthly views)" — link to article. Word count = `bytes / 6` rounded.
- Social media (from MusicBrainz url-rels): "Has social media: {Instagram}, {Twitter}, {Facebook}" — each name clickable to actual URL. Only list platforms found.
- Official website: "Has official website" — clickable link

**Bio analysis bullets (synthesize across Wikipedia, Discogs, Genius, MusicBrainz):**
- When bios exist: "Biographical info on {N} platforms: {Wikipedia}, {Discogs}, {Genius}" — each clickable
  - If real name known: "Real name: {real_name}"
  - Cross-platform consistency: "Consistent identity across {Wikipedia} and {Discogs}: {country}, active since {year}"
- **Bio depth signals** — parse and report what verifiable specifics were found:
  - Location/origin: "Bio mentions location: {city}, {country}"
  - Career timeline: "Bio mentions activity years: {year}–{year}" or "active since {year}"
  - Label/release references: "Bio references label: {label_name}"
  - Collaborator mentions: "Bio names collaborators: {names}"
  - Genre/scene context: "Bio places artist in {scene} in {city}"
  - Education/training: "Bio mentions musical training/education"
  - Display as summary: "Bio contains verifiable details: location ({city}), active since {year}, references {N} collaborators"
- **Bio red flags:**
  - "Bio is generic/boilerplate ({char_count} chars, no specific claims)"
  - "Bio language suggests AI generation" (if detectable)
- When no bios anywhere: "No biographical information found on any platform"

**Negative bullet (single consolidated line):**
- "Not found on {list}" — only platforms in this section: Deezer, YouTube, Bandcamp, Wikipedia, Genius, official website, social media

**Do not show:** "Found on N platforms" — redundant

### 6.2 FAN ENGAGEMENT — Do real people listen to this artist?

**Bullets (show when data exists):**
- Last.fm: "{listener_count} listeners, {scrobble_count} scrobbles on Last.fm" — link to `last.fm/music/{name}`
- Play/listener ratio: "Play-to-listener ratio: {ratio}x" with interpretation:
  - < 2.0: "(low — listeners rarely return)"
  - 2.0–5.0: "(normal — healthy repeat listening)"
  - 5.0–15.0: "(high — dedicated fanbase or background listening)"
  - > 15.0: "(very high — possible bot activity or playlist looping)"
- Deezer fans: "{fan_count} Deezer fans" — only show if > 0

**Negative bullet (single consolidated line):**
- e.g.: "0 Deezer fans · Not found on Last.fm"
- Show disparity when notable: "0 Deezer fans despite 95K Last.fm scrobbles"

### 6.3 CREATIVE HISTORY — What have they actually made?

**Bullets (show when data exists):**
- Release breakdown: "{N} albums, {N} singles, {N} EPs — averaging {X} singles/year and {Y} albums+EPs/year"
  - If only singles with 0 albums, flag: "{N} singles, 0 albums"
- Genius catalog: "On Genius ({song_count} songs)" — link to Genius artist URL
- Top track concentration (Deezer rank data):
  - "Top track holds {pct}% of total Deezer rank · Top 2: {pct}% · Top 3: {pct}%"
  - Top 2 ≥ 90%: "(extreme — almost certainly playlist-placed, not organic)" — strong red
  - Top 2 80–89%: "(high — catalog heavily dependent on 1–2 tracks)" — moderate red
  - Top 2 70–79%: "(elevated — worth noting)" — weak red
  - Top 2 < 70%: healthy distribution — don't show this bullet
- Average track duration: "Average track length: {min}:{sec}" with interpretation:
  - < 1:30: "(short — optimized for streaming payouts)"
  - 1:30–3:00: "(normal range)"
  - > 3:00: "(full-length tracks)"
  - Flag low variation: "with very low variation ({stdev}s)" if stdev < 10s across ≥5 tracks
- Collaborators: "{N} collaborators found" — MusicBrainz artist relations and Genius credits only (NOT Deezer related artists)
- Groups/bands: "Member of {N} groups: {group names}" — from MusicBrainz

**Negative bullet (single consolidated line):**
- e.g.: "0 albums across {N} singles · No collaborators found · Not on Genius"

**Recommended addition (backlog):**
- Same-day releases: "{N} releases on a single day" — show when ≥ 3

### 6.4 IRL PRESENCE — Does this artist exist in the physical world?

**Positive bullets (show when found):**
- Setlist.fm: "{N} concerts on Setlist.fm" — link to setlist.fm page
- Discogs: "{N} physical releases on Discogs ({vinyl_count} vinyl, {cd_count} CD, {cassette_count} cassette)" — link to Discogs page. Discogs is the **sole home** for physical release data.

**Negative bullet (single line, never "No data"):**
- "No concert history on Setlist.fm · No physical releases on Discogs"
- If only one missing: "No concert history on Setlist.fm" or "No physical releases on Discogs"

**Recommended addition (backlog):**
- Bandsintown: "{N} upcoming shows scheduled"

### 6.5 INDUSTRY SIGNALS — Formal music industry recognition

**Bullets (show when data exists):**
- MusicBrainz entry: "MusicBrainz: {type} from {country}, active since {begin_date}" — link to MB page
- MusicBrainz completeness:
  - Rich: "MusicBrainz: rich profile — {N} relationships (recordings, releases, URL links, artist relations)"
  - Stub: "MusicBrainz: minimal stub entry (name only, no relationships)"
- ISNI: "ISNI registered ({isni_code})" — link to isni.org
- IPI: "IPI registered ({ipi_code})"
- ASCAP/BMI: "Registered with {ASCAP|BMI}"
  - If PFC publisher: "Published by {publisher_name}" — red flag

**Negative bullet (single consolidated line):**
- e.g.: "No MusicBrainz entry · No ISNI/IPI codes · No ASCAP/BMI registration"

### 6.6 BLOCKLIST STATUS — Known bad actor matches

**Clean result:**
- "Clean across all blocklists" — single green line

**Flagged results (show each match separately — do NOT consolidate):**
- "Artist name matches known AI artist database ({database_name})"
- "Label '{label_name}' matches PFC distributor blocklist"
- "Songwriter '{songwriter_name}' matches PFC songwriter database"
- "Distributor matches known PFC entity: {entity_name}"
- Include specific file/database name for auditability

**Recommended additions (backlog):**
- ISRC registrant match
- Network proximity: "Shares {N} songwriters/producers with {N} known PFC artists"

---

## Part 7: General Display Rules

1. **Clickable links everywhere.** Every platform name with a found result links to the actual artist profile. New tab.

2. **Scope negatives to the category.** Each category's negative bullet only mentions platforms it checks:
   - Platform Presence: Deezer, YouTube, Bandcamp, Wikipedia, Genius, official website, social media
   - Fan Engagement: Last.fm, Deezer fans
   - Creative History: Deezer catalog, Genius, MusicBrainz/Genius credits
   - IRL Presence: Setlist.fm, Discogs
   - Industry Signals: MusicBrainz, ISNI/IPI, ASCAP/BMI
   - Blocklist: N/A (always clean or flagged)

3. **One negative bullet per category.** Consolidate all not-found items into a single line.

4. **Never show "No data."** Always name the specific source that was checked and came back empty.

5. **No redundant counts.** Don't show "Found on N platforms" or "N green / N red flags."

6. **Deduplicate evidence.** Each fact renders exactly once. The current report shows duplicates (e.g., "Wikipedia page exists" AND "Wikipedia article (11,256 bytes)"). Use this spec as the canonical bullet list.

7. **Interpret numbers.** Add parenthetical meaning (play/listener ratio, track duration, etc.) so readers don't need domain expertise.

8. **Order within category.** Green flags first, neutral, red flags, then the negative line last.

---

## Part 8: UX Improvements

### Radar chart
Restore the 6-axis radar chart in the expanded artist detail. Desktop: left of category bars. Mobile: stacked above. Fill polygon uses verdict color.

### Mobile responsiveness
- Collapsed cards stack cleanly on narrow screens
- Verdict bar: consider vertical stacked bar on mobile
- Category score bars should not overflow
- Platform checkmark row wraps gracefully

### Timed-out artists (backlog)
Current timeout rate is 43%. Track and reduce over time:
- Increase per-artist timeout threshold
- Retry timed-out artists after initial pass completes
- Parallelize API calls more aggressively
- Cache cross-artist API results
- Track timeout rate per data source to identify bottleneck

---

## Part 9: End-to-End Checklist

### Backend (evaluation engine)
- [ ] Implement per-category scoring tables exactly as defined in Part 5
- [ ] Blocklist: AI blocklist or PFC label match → score 0. Songwriter/distributor → -80.
- [ ] Physical releases (Discogs) scored as strong (+30) — biggest IRL signal
- [ ] Top track concentration: 3 tiers (≥90% strong, 80–89% moderate, 70–79% weak)
- [ ] Bio analysis: parse for verifiable details (location, years, labels, collaborators), score per 5.1
- [ ] Genius: binary presence (any songs = +5 weak)
- [ ] MusicBrainz completeness: count relationships → rich (≥10) / moderate (3–9) / stub (0–2)
- [ ] Remove: mood-word track title analysis, touring geography, named tours
- [ ] Evidence deduplication: don't emit both raw + summary items for same signal
- [ ] Store profile URLs per platform in artist result
- [ ] Store bio text from Discogs/Genius/Wikipedia for frontend display
- [ ] Store MusicBrainz relationship count in artist result
- [ ] Store Deezer per-track rank data for concentration calculation

### Frontend (React rendering)
- [ ] Render bullets per Part 6 — one bullet per data point, one negative line per category
- [ ] All platform names clickable links (new tab)
- [ ] Category score colors: 4-tier (green/light-green/orange/red) + gray for no-data
- [ ] Blocklist: binary (100 = green, <100 = red)
- [ ] Remove: "Found on N platforms", flag counts, scan time, API calls, contamination, Claude AI Deep Dive, Data Sources panel
- [ ] Rename "Authentic" → "Likely Authentic" everywhere
- [ ] Wikipedia: word count (bytes / 6) not bytes
- [ ] "No data" → explicit source names
- [ ] Verdict bar: gray "Not Scanned" segment + nested threat categories
- [ ] Move methodology to /methodology page, replace with one-line link
- [ ] Summary: analyzed count only + timeout warning
- [ ] Deduplicate evidence bullets
- [ ] Collapsed card: score + name + verdict + threat + chevron only
- [ ] Prototype mini 6-segment bar (iterate before shipping)
- [ ] Sort/filter controls (score asc/desc/alpha, filter by verdict)
- [ ] Standardized verdict description templates
- [ ] Accessibility indicators (✓ ○ △ ✗) alongside colors
- [ ] Confidence: visual weight on verdict badge (solid/standard/outlined)
- [ ] Restore radar chart in expanded detail
- [ ] Global color constants used everywhere
- [ ] Mobile responsiveness pass

### Scoring alignment
- [ ] Verdict ranges: Verified 82–100, Likely Authentic 58–81, Inconclusive 38–57, Suspicious 18–37, Likely Artificial 0–17
- [ ] Category colors: ≥70 green, 40–69 light green, 15–39 orange, 0–14 red
- [ ] Blocklist override: any score < 100 = red
- [ ] 0 with no data = gray, not red
