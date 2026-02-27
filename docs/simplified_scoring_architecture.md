# Spotify Auditor — Simplified Scoring Architecture

This document defines the single-pass analysis pipeline for the Spotify Playlist Auditor. It replaces the previous three-tier escalation system (Quick/Standard/Deep) with a unified collect-then-evaluate approach.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    PLAYLIST INPUT                           │
│              Spotify playlist URL or ID                     │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                 PHASE 1: COLLECT                            │
│                                                             │
│  For each artist, collect all evidence concurrently:        │
│                                                             │
│  ┌──────────────────────┐  ┌─────────────────────────────┐  │
│  │  Spotify / Deezer    │  │  External APIs (concurrent) │  │
│  │  (already available) │  │                             │  │
│  │                      │  │  • Genius                   │  │
│  │  • followers         │  │  • Discogs                  │  │
│  │  • monthly_listeners │  │  • MusicBrainz              │  │
│  │  • popularity        │  │  • Setlist.fm               │  │
│  │  • genres            │  │  • Last.fm                  │  │
│  │  • catalog           │  │  • Songkick                 │  │
│  │  • YouTube                  │  │
│  │  • Wikipedia                │  │
│  │  • track durations   │  │                             │  │
│  │  • release dates     │  │  All fire concurrently.     │  │
│  │  • labels            │  │  ~2-3 seconds per artist.   │  │
│  │  • contributors      │  │                             │  │
│  │  • images            │  │                             │  │
│  │  • deezer_fans       │  │                             │  │
│  └──────────────────────┘  └─────────────────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Blocklist Checks (instant, local)                   │   │
│  │                                                      │   │
│  │  • pfc_distributors.json  → label/distributor match  │   │
│  │  • known_ai_artists.json  → artist name match        │   │
│  │  • pfc_songwriters.json   → contributor/credit match  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Entity Database (SQLite, optional)                  │   │
│  │                                                      │   │
│  │  • Prior scan results for this artist/label/writer   │   │
│  │  • Cowriter network overlap with flagged artists     │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Claude AI Analysis (optional, user-toggled)         │   │
│  │                                                      │   │
│  │  • Bio analysis (AI mentions, verifiable claims)     │   │
│  │  • Image analysis (AI artifacts, stock photo)        │   │
│  │  • Synthesis (holistic assessment)                   │   │
│  │                                                      │   │
│  │  Batched: 8 artists per Claude call for bio/synth.   │   │
│  │  Individual calls for image analysis.                │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      │  All evidence merged into a single list
                      │  of typed Evidence objects
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                 PHASE 2: EVALUATE                           │
│                                                             │
│  19 Evidence Collectors → typed flags:                      │
│                                                             │
│  Each produces Evidence objects:                            │
│    • type: red_flag | green_flag | neutral                  │
│    • strength: strong (3pts) | moderate (2pts) | weak (1pt) │
│    • tags: structured metadata (e.g. "pfc_label",           │
│            "content_farm", "ai_generated")                  │
│    • finding: short summary                                 │
│    • source: data source name                               │
│    • detail: longer explanation                              │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Evidence Collectors                                  │  │
│  │                                                       │  │
│  │  From Spotify/Deezer:        From External APIs:      │  │
│  │   1. Platform Presence       12. Genius               │  │
│  │   2. Follower/Fan            13. Discogs              │  │
│  │   3. Catalog                 14. Live Shows           │  │
│  │   4. Duration                15. MusicBrainz          │  │
│  │   5. Release Cadence         16. Social Media         │  │
│  │   6. Label (blocklists)      17. Identity             │  │
│  │   7. Name Pattern            18. Last.fm              │  │
│  │   8. Collaboration           19. Touring Geography    │  │
│  │   9. Credit Network                                   │  │
│  │  10. Genre                   From Claude (optional):  │  │
│  │  11. Track Rank              20. Bio Analysis         │  │
│  │                              21. Image Analysis       │  │
│  │                              22. Synthesis            │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                             │
│                      │                                      │
│                      ▼                                      │
│         ┌─────────────────────┐                             │
│         │   DECISION TREE     │                             │
│         │   (first match wins)│                             │
│         └────────┬────────────┘                             │
│                  │                                          │
│                  ▼                                          │
│         Verdict + Confidence + Score                        │
│                  │                                          │
│                  ▼                                          │
│         Threat Category (if suspicious)                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                 OUTPUT                                       │
│                                                             │
│  Per artist:                                                │
│    • Verdict (enum)                                         │
│    • Confidence (high / medium / low)                       │
│    • Legitimacy Score (0-100)                               │
│    • Threat Category (if applicable)                        │
│    • All evidence with flags, strengths, and details        │
│    • Radar chart category scores (6 dimensions)             │
│                                                             │
│  Per playlist:                                              │
│    • Health score (weighted average of artist verdicts)      │
│    • Contamination count (artists per verdict tier)          │
│    • Artists sorted by severity                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Evidence Data Model

```python
@dataclass
class Evidence:
    finding: str          # Short summary, e.g. "No albums, 47 singles"
    source: str           # Data source, e.g. "Spotify", "Discogs"
    evidence_type: str    # "red_flag" | "green_flag" | "neutral"
    strength: str         # "strong" | "moderate" | "weak"
    detail: str           # Longer explanation
    tags: list[str]       # Structured metadata for matching
                          # e.g. ["content_farm", "catalog"]
```

Use `tags` for all downstream matching (decision tree, threat categories, category scores). Never match on `finding` text — it's for human display only.

### Tag Vocabulary

Use these controlled tags across all evidence collectors:

```
# Blocklist matches
pfc_label           — Label/distributor matches pfc_distributors.json
known_ai_artist     — Name matches known_ai_artists.json
pfc_songwriter      — Contributor matches pfc_songwriters.json

# Behavioral patterns
content_farm        — High-volume singles-only catalog
stream_farm         — Short tracks near 30s payout threshold
cookie_cutter       — Uniform track durations
playlist_stuffing   — Streaming concentrated in top tracks
high_release_rate   — Abnormal release cadence
same_day_release    — Multiple releases on single day

# Positive signals
live_performance    — Concert/tour history exists
physical_release    — Vinyl/CD releases exist
industry_registered — ISNI/IPI codes found
verified_identity   — Real name, aliases, group members known
wikipedia           — Wikipedia article exists
genuine_fans        — High play/listener ratio or follower count

# AI-specific (from Claude analysis)
ai_generated_image  — Profile image flagged as AI
ai_generated_music  — Content flagged as AI-produced
ai_bio              — Bio mentions AI or has ChatGPT style
stock_photo         — Profile image is stock photography
impersonation       — Content uploaded to wrong artist page

# Data availability
api_unconfigured    — API was not queried (distinct from "not found")
not_found           — API was queried, artist not present
```

---

## Decision Tree

Evaluated in order. First match wins. All rules operate on the evidence list using tags and strength counts.

```
Strength weights:
  strong   = 3 points
  moderate = 2 points
  weak     = 1 point

total_green = strong_greens × 3 + moderate_greens × 2 + weak_greens × 1
total_red   = strong_reds × 3 + moderate_reds × 2 + weak_reds × 1
```

### Rules

```
RULE 1: Known AI Artist
  IF: any evidence has tag "known_ai_artist" AND strength == "strong"
  THEN: LIKELY ARTIFICIAL (high confidence)
  Rationale: Direct blocklist match. Instant kill.

      │ no match
      ▼

RULE 2: PFC Label + Behavioral Pattern
  IF: any evidence has tag "pfc_label"
      AND any evidence has tag "content_farm" OR "stream_farm"
  THEN: LIKELY ARTIFICIAL (high confidence)
  Rationale: Known bad distributor + suspicious catalog = confirmed fraud.

      │ no match
      ▼

RULE 3: Overwhelming Red, No Green
  IF: strong_red_count >= 3
      AND strong_green_count == 0
      AND moderate_green_count == 0
  THEN: LIKELY ARTIFICIAL (medium confidence)
  Rationale: Multiple strong negatives with zero counterbalance.

      │ no match
      ▼

RULE 4: Strong Greens Dominate
  IF: strong_green_count >= 2
      AND strong_red_count == 0
      AND total_red < 4
  THEN: VERIFIED ARTIST (high confidence)
  Rationale: Multiple strong legitimacy signals, no strong concerns,
  and limited moderate/weak concerns. The total_red < 4 guard
  prevents verification when moderate reds are piling up.

      │ no match
      ▼

RULE 5: Multi-Platform + Genuine Fanbase
  IF: platform_count >= 3
      AND any evidence has tag "genuine_fans" with strength "strong"
      AND strong_red_count == 0
  THEN: VERIFIED ARTIST (high confidence)
  Rationale: Broad platform presence + strong fan engagement + clean record.

      │ no match
      ▼

RULE 6: Green Strongly Outweighs Red
  IF: total_green >= total_red × 2
      AND total_green >= 4
  THEN: LIKELY AUTHENTIC (medium confidence)

      │ no match
      ▼

RULE 7: Red Strongly Outweighs Green
  IF: total_red >= total_green × 2
      AND total_red >= 4
  THEN: SUSPICIOUS (medium confidence)

      │ no match
      ▼

RULE 8: PFC Label Alone
  IF: any evidence has tag "pfc_label"
  THEN: SUSPICIOUS (medium confidence)
  Rationale: Known PFC distributor is a meaningful signal even
  without corroborating behavioral evidence. Upgraded from low
  to medium confidence — being on a confirmed PFC label is not
  a weak signal.

      │ no match
      ▼

RULE 9: Green > Red
  IF: total_green > total_red
  THEN: LIKELY AUTHENTIC (low confidence)

      │ no match
      ▼

RULE 10: Red > Green
  IF: total_red > total_green
  THEN: SUSPICIOUS (low confidence)

      │ no match
      ▼

DEFAULT: INCONCLUSIVE (low confidence)
  Split into two sub-verdicts for clarity:
    • INSUFFICIENT DATA — if total evidence count < 5
    • CONFLICTING SIGNALS — if total_green >= 4 AND total_red >= 4
```

---

## Score Derivation

Each verdict maps to a score range on the 0–100 legitimacy scale:

```
Verdict              Range     Meaning
─────────────────────────────────────────────
Verified Artist      82-100    Confirmed legitimate
Likely Authentic     58-80     Probably legitimate
Inconclusive         38-56     Can't determine
Suspicious           18-36     Probably fraudulent
Likely Artificial     0-16     Confirmed fraudulent
```

Position within the range is determined by blending confidence and flag balance:

```
confidence_position:
  high   → 0.85
  medium → 0.55
  low    → 0.25

flag_balance:
  net = (strong_greens × 3 + all_greens) - (strong_reds × 3 + all_reds)
  net_frac = clamp(net / max_possible, -1, 1)
  flag_position = (net_frac + 1) / 2     # maps [-1, 1] → [0, 1]

final_position = confidence_position × 0.7 + flag_position × 0.3
score = range_low + final_position × (range_high - range_low)
```

---

## Threat Category Inference

Only assigned when verdict is SUSPICIOUS or LIKELY ARTIFICIAL. Uses evidence tags. First match wins.

```
CHECK 1: Impersonation
  IF: any evidence has tag "impersonation"
  THEN: Category 4 — AI Impersonation
  (AI tracks uploaded to a real artist's page)

      │ no match
      ▼

CHECK 2: PFC + AI Hybrid
  IF: any evidence has tag "pfc_label" or "pfc_songwriter"
      AND any evidence has tag "ai_generated_music" or "ai_generated_image" or "ai_bio"
  THEN: Category 1.5 — PFC + AI Hybrid
  (PFC infrastructure using AI-generated content)

      │ no match
      ▼

CHECK 3: Independent AI
  IF: any evidence has tag "ai_generated_music" or "ai_generated_image" or "ai_bio"
      AND NO evidence has tag "pfc_label" or "pfc_songwriter"
  THEN: Category 2 — Independent AI Artist
  (AI content not affiliated with PFC pipeline)

      │ no match
      ▼

CHECK 4: PFC Ghost
  IF: any evidence has tag "pfc_label" or "pfc_songwriter"
  THEN: Category 1 — PFC Ghost Artist
  (Human-made, commissioned at flat fee under fake name)

      │ no match
      ▼

CHECK 5: Fraud Farm
  IF: any evidence has tags "high_release_rate" AND "stream_farm"
  THEN: Category 3 — AI Fraud Farm
  (Mass AI content for streaming fraud)

      │ no match
      ▼

DEFAULT: Category 1 — PFC Ghost Artist
  (Most common threat type; safe default assumption)
```

---

## Evidence Collectors Reference

### From Spotify / Deezer data (instant)

| # | Collector | Key Signals | Tags Used |
|---|-----------|-------------|-----------|
| 1 | Platform Presence | Count of platforms where artist exists | genuine_fans, not_found |
| 2 | Follower/Fan | Follower count, follower/listener ratio | genuine_fans, playlist_stuffing |
| 3 | Catalog | Album vs single count, total releases | content_farm |
| 4 | Duration | Average track length, duration variance | stream_farm, cookie_cutter |
| 5 | Release Cadence | Releases per month, same-day releases | high_release_rate, same_day_release |
| 6 | Label | Labels/contributors vs 3 blocklists | pfc_label, known_ai_artist, pfc_songwriter |
| 7 | Name Pattern | Name vs AI blocklist, generic patterns | known_ai_artist |
| 8 | Collaboration | Collaborator count, related artists | — |
| 9 | Credit Network | Shared producers with flagged artists | pfc_songwriter |
| 10 | Genre | Genre tag count | — |
| 11 | Track Rank | Rank concentration, average rank | playlist_stuffing |

### From External APIs (concurrent, ~2-3s)

| # | Collector | Source | Key Signals | Tags Used |
|---|-----------|--------|-------------|-----------|
| 12 | Genius | Genius API | Song count, bio, verified status, followers | not_found, genuine_fans |
| 13 | Discogs | Discogs API | Physical releases, labels vs blocklist, bio | physical_release, pfc_label, not_found |
| 14 | Live Shows | Setlist.fm | Show count, venue details | live_performance, not_found |
| 15 | MusicBrainz | MusicBrainz API | Metadata richness, labels vs blocklist, ISNI/IPI | industry_registered, pfc_label, not_found |
| 16 | Social Media | Genius + Discogs + MB | Social link count, Wikipedia, verified | wikipedia, verified_identity |
| 17 | Identity | Discogs + MB | Real name, aliases, group members, ISNI, IPI | verified_identity, industry_registered |
| 18 | Last.fm | Last.fm API | Listeners, play/listener ratio, bio | genuine_fans, not_found |
| 19 | Touring | Setlist.fm + Songkick | Countries, cities, tour names, on-tour status | live_performance, touring_geography |
| 20 | Wikipedia | Wikipedia API | Article length, monthly views, categories | wikipedia |
| 21 | YouTube | YouTube Data API | Subscribers, video count, music videos | youtube_presence |

### From Claude AI (optional)

| # | Collector | Input | Key Signals | Tags Used |
|---|-----------|-------|-------------|-----------|
| 22 | Bio Analysis | All available bios | AI mentions, verifiable claims, geographic specificity | ai_bio |
| 23 | Image Analysis | Profile image | AI artifacts, stock photo, human photo | ai_generated_image, stock_photo |
| 24 | Synthesis | All prior evidence | Holistic PFC/AI/legitimate assessment | ai_generated_music, impersonation |

---

## Radar Chart Dimensions (6 categories)

For visualization. Each dimension scored 0–100 using evidence tags (not string matching on findings).

| Dimension | What it measures | Key inputs |
|-----------|-----------------|------------|
| Platform Presence | How widely the artist exists across services | Platform count, YouTube, Wikipedia, social media, Genius followers |
| Fan Engagement | Real fan activity vs algorithmic/passive | Deezer fans, Last.fm ratio, Genius followers |
| Creative History | Evidence of genuine artistic output | Albums, Genius songs, collaborators, release cadence |
| IRL Presence | Physical-world evidence of the artist | Setlist.fm/Songkick shows, Discogs physical releases, tour names, countries |
| Industry Signals | Professional music industry registration | ISNI, IPI, ASCAP/BMI, MusicBrainz metadata, Discogs bio/quality |
| Blocklist Status | Matches against known fraud databases | PFC distributors, known AI artists, PFC songwriters, entity DB |

---

## Playlist-Level Output

```
Playlist Health Score:
  Map each artist verdict to a health value:
    Verified Artist     → 100
    Likely Authentic    →  85
    Inconclusive        →  50
    Suspicious          →  25
    Likely Artificial   →   0

  health_score = average of all artist health values

Contamination Report:
  Count of artists at each verdict tier.
  Raw count is reported alongside the health score so that
  a single fraudulent artist in an otherwise clean playlist
  is not hidden by averaging.

Sort Order:
  Artists sorted by verdict severity (worst first),
  then by score within each verdict tier.
```

---

## Fast Mode (Optional Optimization)

For large playlists (100+ artists), skip external API calls for artists that are obviously legitimate from Spotify data alone:

```
Skip external APIs if ALL of the following are true:
  • followers >= 500,000
  • has Wikipedia link in external_urls
  • has 3+ genres assigned
  • has 5+ albums
  • not on any blocklist

These artists are marked VERIFIED ARTIST (high confidence)
without running the full evidence pipeline.
```

This is a performance optimization, not an architectural tier. The decision tree is the same for all artists that go through full analysis.
