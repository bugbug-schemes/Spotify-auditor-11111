# Spotify Auditor — Scoring & Logic Deep Review

This document contains the complete scoring and decision logic for the Spotify Playlist Auditor. It's intended for review, critique, and iteration on the design.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Data Model](#2-data-model)
3. [Tier 1: Quick Scan (Spotify/Deezer Only)](#3-tier-1-quick-scan)
4. [Tier 2: Standard Scan (External APIs)](#4-tier-2-standard-scan)
5. [Evidence Pipeline (Primary System)](#5-evidence-pipeline)
6. [Decision Tree](#6-decision-tree)
7. [Score Derivation](#7-score-derivation)
8. [Threat Category Inference](#8-threat-category-inference)
9. [Tier 3: Deep Scan (Claude AI)](#9-tier-3-deep-scan)
10. [Playlist-Level Aggregation](#10-playlist-level-aggregation)
11. [Category Scores (Radar Chart)](#11-category-scores)
12. [Blocklists](#12-blocklists)
13. [Entity Database Intelligence](#13-entity-database-intelligence)
14. [Open Questions & Known Issues](#14-open-questions--known-issues)

---

## 1. Architecture Overview

The system has **two parallel scoring paths** that coexist:

### Path A: Legacy Weighted Scores (Supplementary)
- Quick Scan → 0-100 **suspicion** score (higher = more suspicious)
- Standard Scan → 0-100 suspicion score (blends Quick 40% + external 60%)
- These are **inverted** at report time: `final_score = 100 - tier_score` to convert to legitimacy scale

### Path B: Evidence-Based Evaluation (Primary)
- Collects ~19 evidence collectors producing red flags, green flags, and neutral notes
- Each piece of evidence has a `strength` (strong/moderate/weak)
- Decision tree walks the flags → produces a **Verdict** (enum) + **confidence** (high/medium/low)
- Verdict is then converted to a 0-100 legitimacy score via `_verdict_to_score()`

**Path B is the primary system.** Path A is retained as supplementary data for backward compatibility. When Path B is available, it determines the final score. Path A is only used as a fallback when no evidence evaluation exists.

### Escalation Flow
```
Quick Scan (always runs)
    │
    ├── Quick suspicion score > 30 → run Standard Scan
    │       │
    │       └── Standard suspicion score > 50 → run Deep Scan (Claude AI)
    │
    └── Evidence evaluation runs in parallel using all available data
```

**Note:** The escalation thresholds use the **suspicion** score (old scale), not the legitimacy score. A suspicion score > 30 means "suspicious enough to investigate further," which maps to a legitimacy score < 70.

---

## 2. Data Model

### ArtistInfo (from Spotify/Deezer scraping)
Core fields used by scoring:
- `followers`, `monthly_listeners`, `popularity` (0-100 Spotify metric)
- `genres: list[str]`
- `album_count`, `single_count`
- `track_durations: list[int]` (milliseconds)
- `release_dates: list[str]`
- `labels: list[str]`
- `contributors: list[str]`, `contributor_roles: dict[str, list[str]]`
- `related_artist_names: list[str]`
- `track_titles: list[str]`
- `track_ranks: list[int]` (Deezer rank metric)
- `top_track_popularities: list[int]`
- `image_url`, `image_width`, `image_height`
- `external_urls: dict[str, str]`
- `deezer_fans: int`
- `bio: str`

### ExternalData (from Standard-tier API lookups)
55 fields aggregated from 6 APIs:
- **Genius** (9 fields): found, song_count, description, social names, verified status, followers, alternate_names
- **Discogs** (10 fields): found, physical/digital/total releases, formats, labels, profile, realname, social_urls, members, groups, data_quality
- **Setlist.fm** (8 fields): found, total_shows, first/last_show, venues, cities, countries, tour_names
- **MusicBrainz** (12 fields): found, type, country, begin_date, labels, urls, genres, aliases, ISNIs, IPIs, gender, area
- **Last.fm** (6 fields): found, listeners, playcount, listener_play_ratio, tags, similar_artists, bio_exists

### Evidence (individual finding)
```python
@dataclass
class Evidence:
    finding: str          # Short summary
    source: str           # Data source
    evidence_type: str    # "red_flag", "green_flag", "neutral"
    strength: str         # "strong", "moderate", "weak"
    detail: str           # Longer explanation
```

### Verdict (enum)
```
VERIFIED_ARTIST    → Score 80-100
LIKELY_AUTHENTIC   → Score 55-79
INCONCLUSIVE       → Score 35-54
SUSPICIOUS         → Score 15-34
LIKELY_ARTIFICIAL  → Score 0-14
```

---

## 3. Tier 1: Quick Scan

**10 signals**, each produces a **0-100 suspicion sub-score** (higher = more suspicious). Final Quick score is a weighted combination.

### Weights (must sum to 1.0)
| Signal | Weight | What it measures |
|--------|--------|-----------------|
| follower_listener_ratio | 0.15 | Monthly listeners vs followers disparity |
| release_cadence | 0.15 | Releases per month |
| genre_absence | 0.10 | No Spotify genre tags |
| external_url_absence | 0.10 | No links besides Spotify |
| catalog_size | 0.10 | Album/single composition |
| track_duration_uniformity | 0.10 | Short, cookie-cutter tracks |
| playlist_placement | 0.10 | High popularity with low followers |
| popularity_follower_mismatch | 0.10 | Top track popularity vs follower count |
| image_quality | 0.05 | Missing or low-res profile image |
| name_pattern | 0.05 | Generic/blocklisted name patterns |

### Signal Details

#### follower_listener_ratio (weight: 0.15)
Uses real follower/monthly_listener ratio when available, falls back to popularity proxy:
| Condition | Score |
|-----------|-------|
| monthly_listeners > 0 and followers = 0 | 90 |
| ratio < 0.001 | 90 |
| ratio < 0.005 | 70 |
| ratio < 0.01 | 45 |
| ratio < 0.03 | 25 |
| ratio >= 0.03 | 5 |
| (fallback) 0 followers, popularity > 20 | 90 |
| (fallback) 0 followers | 60 |

**Context:** Real artists typically have 3-15% of monthly listeners as followers. PFC/ghost artists have massive listener counts from playlist placement but near-zero followers because nobody deliberately follows them.

#### genre_absence (weight: 0.10)
| Condition | Score |
|-----------|-------|
| No genres | 70 |
| 1 genre | 20 |
| 2+ genres | 0 |

**Context:** Spotify auto-assigns genres to established artists. No genres = too new or not recognized.

#### image_quality (weight: 0.05)
| Condition | Score |
|-----------|-------|
| No profile image | 80 |
| Image width < 300px | 40 |
| Image present | 0 |

#### external_url_absence (weight: 0.10)
| Condition | Score |
|-----------|-------|
| Only Spotify URL (no website, socials) | 50 |
| Has external URLs | 0 |

#### catalog_size (weight: 0.10)
| Condition | Score |
|-----------|-------|
| 0 albums, 0 singles | 60 |
| 0 albums, > 20 singles | 70 |
| 0 albums, ≤ 5 singles | 30 |
| < 2 albums, > 50 singles | 75 |
| Otherwise | 5 |

**Context:** Singles-only catalogs with high volume are a content farm indicator. Real artists eventually release albums.

#### track_duration_uniformity (weight: 0.10)
Additive scoring up to 100:
| Condition | Points |
|-----------|--------|
| avg < 90s | +40 |
| avg < 120s | +20 |
| stdev < 10s (≥5 tracks) | +35 |
| stdev < 20s | +15 |

**Context:** Stream farms create short tracks (just past the 30-second payout threshold) to maximize royalties per stream. Normal songs average 3-4 minutes.

#### release_cadence (weight: 0.15)
| Condition | Score |
|-----------|-------|
| Multiple releases same day | 80 |
| > 8 releases/month | 90 |
| > 4 releases/month | 65 |
| > 2 releases/month | 35 |
| ≤ 2 releases/month | 5 |
| Only 0-1 releases | 10 |

#### playlist_placement (weight: 0.10)
| Condition | Score |
|-----------|-------|
| popularity ≥ 40, followers < 500 | 80 |
| popularity ≥ 30, followers < 200 | 70 |
| popularity ≥ 20, followers < 50 | 60 |
| Otherwise | 5 |

#### popularity_follower_mismatch (weight: 0.10)
| Condition | Score |
|-----------|-------|
| No top tracks data | 20 |
| Max track pop > 50 & followers < 300 | 80 |
| Avg track pop > 30 & followers < 500 | 55 |
| Otherwise | 5 |

#### name_pattern (weight: 0.05)
Additive scoring:
| Condition | Points |
|-----------|--------|
| Matches known AI artist blocklist | 100 |
| Generic "Adjective Noun" pattern | +25 |
| Single lowercase word (3-15 chars) | +20 |
| Very short name (≤3 chars) | +15 |

---

## 4. Tier 2: Standard Scan

**7 signals**, each 0-100 suspicion. Runs only when Quick suspicion > 30.

### Weights
| Signal | Weight | Source |
|--------|--------|--------|
| quick_score | 0.40 | Carry-forward from Quick tier |
| genius_credits | 0.12 | Genius API |
| discogs_physical | 0.12 | Discogs API |
| live_show_history | 0.12 | Setlist.fm |
| label_blocklist_match | 0.10 | MusicBrainz labels vs PFC blocklist |
| musicbrainz_presence | 0.08 | MusicBrainz metadata richness |
| deezer_cross_check | 0.06 | Deezer fan validation |

### Signal Details

#### genius_credits (weight: 0.12)
| Condition | Score |
|-----------|-------|
| API not configured | 50 |
| Not found on Genius | 75 |
| Found but 0 songs | 80 |
| 1-3 songs | 50 |
| 4-10 songs | 25 |
| 11+ songs | 5 |

#### discogs_physical (weight: 0.12)
| Condition | Score |
|-----------|-------|
| Not found | 70 |
| Found, 0 releases | 75 |
| Digital-only releases | 55 |
| No physical releases | 65 |
| 1 physical release | 25 |
| 2-4 physical releases | 10 |
| 5+ physical releases | 0 |

**Context:** Physical releases (vinyl/CD) require real investment and are virtually never produced by ghost/AI artists.

#### live_show_history (weight: 0.12)
| Condition | Score |
|-----------|-------|
| APIs not configured | 50 |
| 0 shows | 80 |
| 1-5 shows | 40 |
| 6-20 shows | 15 |
| 21+ shows | 0 |

#### musicbrainz_presence (weight: 0.08)
Starts at 30, subtracts for richness:
| Condition | Deduction |
|-----------|-----------|
| Has type (Person/Group) | -10 |
| Has country | -5 |
| Has begin_date | -10 |
| Has disambiguation | -5 |

Minimum: 0

#### label_blocklist_match (weight: 0.10)
| Condition | Score |
|-----------|-------|
| No blocklist loaded | 0 |
| No label info available | 30 |
| Labels match PFC blocklist | 90 |
| Labels don't match | 5 |

#### deezer_cross_check (weight: 0.06)
| Condition | Score |
|-----------|-------|
| Lookup failed | 50 |
| Not found on Deezer | 65 |
| Name mismatch | 55 |
| 0 fans | 60 |
| < 100 fans | 40 |
| 100-999 fans | 20 |
| 1000+ fans | 5 |

### standard_scan_from_external()
There is a second Standard scorer (`standard_scan_from_external`) that recomputes Standard-tier scores from pre-fetched `ExternalData` without re-querying APIs. It mirrors the same thresholds as above. This exists because the CLI already fetches all external data for the evidence pipeline, so we can avoid duplicate API calls.

---

## 5. Evidence Pipeline (Primary System)

The evidence pipeline is the **primary scoring system**. It runs ~19 evidence collectors that produce typed `Evidence` objects. Each has a type (red_flag/green_flag/neutral) and strength (strong/moderate/weak).

### Core Evidence Collectors (from ArtistInfo)

#### 1. Platform Presence
- ≥5 platforms found → strong green
- ≥3 platforms → strong green
- 2 platforms → moderate green
- ≤1 platform → weak red

Platforms tracked: Spotify, Deezer, MusicBrainz, Genius, Discogs, Setlist.fm, Last.fm (7 total)

#### 2. Follower/Fan Evidence
| Fans | Type | Strength |
|------|------|----------|
| ≥100K | green | strong |
| ≥10K | green | moderate |
| ≥1K | neutral | weak |
| 1-999 | red | weak |
| 0 | neutral | weak |

Also checks monthly_listeners-to-followers ratio:
- ratio < 0.005 → **strong red** ("playlist-driven streams without real fans")
- ratio < 0.03 → **moderate red**

#### 3. Catalog Evidence
- 3+ albums → moderate green ("albums require significant creative investment")
- 1-2 albums → weak green
- 0 albums, 0 singles → moderate red ("empty catalog")
- 0 albums, >20 singles → **strong red** ("content farm pattern")
- 0 albums, 11-20 singles → moderate red

#### 4. Duration Evidence
- avg < 90s → **strong red** ("stream farm short tracks")
- avg < 120s → moderate red
- stdev < 10s (≥5 tracks) → moderate red ("cookie-cutter")
- avg ≥ 180s & stdev ≥ 30s → weak green ("normal tracks")

#### 5. Release Evidence
Separates albums/month from singles/month:
- Albums: >2/mo → strong red, >1/mo → moderate red
- Singles: >6/mo → strong red, >3/mo → moderate red
- ≤1.5 releases/mo with ≥5 releases → weak green ("steady pace")
- All releases same day → strong red

#### 6. Label Evidence
Checks against 3 blocklists:
- PFC distributors match → **strong red**
- Known AI artist list match → **strong red**
- PFC songwriter match in contributors → **strong red**
- No blocklist matches → weak neutral

#### 7. Name Evidence
- Known AI artist blocklist match → **strong red** (terminates early)
- Generic "Adjective Noun" pattern → weak red
- ≥70% of track titles use mood/atmosphere words (≥4 tracks) → moderate red

Mood word list includes: calm, peaceful, gentle, soft, quiet, serene, dreamy, morning, rain, ocean, forest, etc. (~50 words)

#### 8. Collaboration Evidence
- ≥3 collaborators → moderate green
- 1-2 collaborators → weak green
- ≥5 related artists on Deezer → moderate green
- 1-4 related artists → weak green

#### 9. Credit Network Evidence
- Contributors match PFC songwriter watchlist → **strong red**
- Single producer credits all tracks (≥5 tracks) → weak red

#### 10. Genre Evidence
- No genres assigned → weak red
- ≥3 genres → weak green

#### 11. Track Rank Evidence
- Top 2 tracks hold ≥90% of total rank → moderate red ("playlist stuffing")
- Avg rank ≥500K → moderate green
- Avg rank ≥100K → weak green

### External API Evidence Collectors (from ExternalData)

#### 12. Genius Evidence
| Condition | Type | Strength |
|-----------|------|----------|
| Not found | red | moderate |
| 0 songs | red | moderate |
| 1-4 songs | green | weak |
| 5-19 songs | green | moderate |
| 20+ songs | green | strong |
| Has bio | green | weak |

#### 13. Discogs Evidence
| Condition | Type | Strength |
|-----------|------|----------|
| Not found | red | moderate |
| 0 releases | red | weak |
| Digital-only | neutral | weak |
| 1-2 physical releases | green | moderate |
| 3-9 physical | green | strong |
| 10+ physical | green | strong |
| Discogs labels match PFC | red | strong |
| 2+ non-PFC labels | green | weak |

Also checks Discogs bio content:
- ≥200 chars + career keywords + year references → **strong green**
- ≥200 chars → moderate green
- ≥50 chars + career keywords → moderate green
- ≥50 chars → weak green

Career keywords checked: "born", "grew up", "formed in", "Grammy", "toured", "festival", "signed to", "debut album", "collaborated with", etc.

#### 14. Live Show Evidence
| Condition | Type | Strength |
|-----------|------|----------|
| Not found on Setlist.fm | red | weak |
| Found, 0 shows | neutral | weak |
| 1-9 shows | green | moderate |
| 10-49 shows | green | strong |
| 50+ shows | green | strong (with venue details) |
| No shows anywhere | red | moderate |

#### 15. MusicBrainz Evidence
Richness score from type + country + begin_date + labels:
| Richness | Type | Strength |
|----------|------|----------|
| ≥3 | green | moderate |
| 1-2 | green | weak |
| 0 (sparse) | neutral | weak |
| Not found | red | weak |

Also checks MusicBrainz labels vs PFC blocklist → **strong red** if match.

#### 16. Social Media Evidence
Aggregates social links from Genius + Discogs + MusicBrainz:
| Social count | Type | Strength |
|-------------|------|----------|
| ≥4 | green | strong |
| 2-3 | green | moderate |
| 1 | green | weak |
| 0 (when ≥2 APIs checked) | red | moderate |

Also:
- Genius verified → moderate green
- ≥1000 Genius followers → moderate green
- ≥100 Genius followers → weak green
- Has Wikipedia article → **strong green**

#### 17. Identity Evidence
- Discogs bio with career keywords + year references → **strong green**
- Real name known → moderate green
- Group members listed → moderate green
- ISNI identifier → **strong green** (professionally registered)
- IPI code → **strong green** (collecting society registered)
- MusicBrainz genres → weak green
- Multiple aliases (≥3) → moderate green

#### 18. Last.fm Evidence
| Condition | Type | Strength |
|-----------|------|----------|
| Not found | red | moderate |
| Found | green | moderate |
| Play/listener ratio ≥10 | green | strong ("genuine fans who return") |
| Ratio < 2 with ≥100 listeners | red | moderate ("passive/algorithmic") |
| < 50 listeners | red | weak |
| Has bio | green | weak |

#### 19. Touring Geography Evidence
- Named tours → moderate green
- ≥5 countries → **strong green**
- 2-4 countries → moderate green
- ≥3 cities → weak green

---

## 6. Decision Tree

The decision tree walks the collected evidence to produce a verdict. Evidence is weighted by strength:
- Strong = 3 points
- Moderate = 2 points
- Weak = 1 point

```
total_green_strength = strong_greens × 3 + moderate_greens × 2 + weak_greens × 1
total_red_strength   = strong_reds × 3 + moderate_reds × 2 + weak_reds × 1
```

### Rules (evaluated in order, first match wins)

**Rule 1: Known AI Artist Name**
If any strong red flag from "Blocklist" source contains "name" and "known AI artist" → **Likely Artificial** (high confidence)

**Rule 2: PFC Label + Content Farm**
If PFC blocklist label match AND content farm/stream farm pattern → **Likely Artificial** (high confidence)

**Rule 3: Multiple Strong Reds, No Greens**
If ≥3 strong red flags AND 0 strong greens AND 0 moderate greens → **Likely Artificial** (medium confidence)

**Rule 4: Strong Greens Dominate**
If ≥2 strong green flags AND 0 strong red flags → **Verified Artist** (high confidence)

**Rule 5: Multi-Platform + Large Fanbase**
If ≥2 platforms AND ≥50K Deezer fans AND 0 strong reds → **Verified Artist** (high confidence)

**Rule 6: Green Evidence Strongly Outweighs**
If `green_strength >= red_strength × 2` AND `green_strength >= 4` → **Likely Authentic** (medium confidence)

**Rule 7: Red Evidence Strongly Outweighs**
If `red_strength >= green_strength × 2` AND `red_strength >= 4` → **Suspicious** (medium confidence)

**Rule 8: PFC Label Alone**
If PFC blocklist match (without triggering Rules 1-7) → **Suspicious** (low confidence)

**Rule 9: Green > Red**
If `green_strength > red_strength` → **Likely Authentic** (low confidence)

**Rule 10: Red > Green**
If `red_strength > green_strength` → **Suspicious** (low confidence)

**Default: Inconclusive** (low confidence)

---

## 7. Score Derivation

### `_verdict_to_score()`: Verdict → 0-100 Legitimacy Score

Each verdict maps to a score range:
```
Verified Artist:    80-100
Likely Authentic:   55-79
Inconclusive:       35-54
Suspicious:         15-34
Likely Artificial:  0-14
```

Position within the range is determined by blending:
1. **Confidence** (70% weight):
   - high → 0.85 position
   - medium → 0.55 position
   - low → 0.25 position

2. **Flag balance** (30% weight):
   ```
   net = (strong_greens × 3 + all_greens) - (strong_reds × 3 + all_reds)
   net_frac = clamp(net / max_possible, -1, 1)
   flag_position = (net_frac + 1) / 2   # map [-1,1] → [0,1]
   ```

Final: `position = confidence × 0.7 + flag_position × 0.3`
Score: `lo + position × (hi - lo)`

### Legacy Fallback
When no evidence evaluation exists:
```python
final_score = max(0, 100 - tier_score)
```
Where `tier_score` is the deepest tier's suspicion score.

---

## 8. Threat Category Inference

Only assigned when verdict is **Suspicious** or **Likely Artificial**. Uses keyword matching on red flag text.

### With Evidence Evaluation
1. Check for keyword patterns in red flag findings + details:
   - `has_pfc`: "pfc" or "content farm"
   - `has_ai`: "ai generat", "ai_generated", "ai-generated"
   - `has_ghost`: "ghost" or "pfc_ghost"
   - `has_impersonation`: "impersonat"

2. Also check Claude synthesis red flags specifically:
   - `synth_pfc`: "pfc" in synthesis findings
   - `synth_ai`: "ai" in synthesis findings

3. Assignment (first match):
   | Condition | Category | ID |
   |-----------|----------|-----|
   | has_impersonation | AI Impersonation | 4 |
   | has_pfc AND has_ai | PFC + AI Hybrid | 1.5 |
   | synth_ai AND NOT synth_pfc | Independent AI Artist | 2 |
   | has_ai AND NOT has_pfc AND NOT has_ghost | Independent AI Artist | 2 |
   | has_pfc OR has_ghost | PFC Ghost Artist | 1 |
   | cadence_raw ≥ 65 AND duration_raw ≥ 50 | AI Fraud Farm | 3 |
   | (default for Suspicious/Likely Artificial) | PFC Ghost Artist | 1 |

### Legacy Fallback (no evidence evaluation)
Only fires when `final_score < 30` (confused — this means it **doesn't** assign categories for suspicious artists with the legacy scorer? See Open Questions):
| Condition | Category |
|-----------|----------|
| name_raw ≥ 100 | Independent AI Artist (2) |
| cadence_raw ≥ 65 & duration_raw ≥ 50 | AI Fraud Farm (3) |
| catalog_raw ≥ 50 & score ≥ 40 | PFC Ghost (1) |
| score ≥ 50 | PFC Ghost (1) |

---

## 9. Tier 3: Deep Scan (Claude AI)

Runs when Standard suspicion > 50. Uses Claude Sonnet for three analyses:

### Bio Analysis
Sends all available bio text (Spotify + Genius + Discogs) to Claude with structured prompts asking about:
- AI/ghost mentions
- ChatGPT-style writing
- Verifiable claims
- Geographic specificity
- Career timeline
- Red flags

Structured response parsed into evidence:
- `AI_MENTIONED: YES` → strong red
- `VERDICT: SUSPICIOUS` → red (strength from confidence)
- `VERDICT: AUTHENTIC` → green (strength from confidence)
- Geographic specificity → weak green/red
- Verifiable claims → moderate green

### Image Analysis
Downloads profile image, sends to Claude vision:
- AI_GENERATED with artifacts → red (strength from confidence)
- STOCK_PHOTO → moderate red
- ABSTRACT_ART/LOGO/OTHER → weak red
- HUMAN_PHOTO with no artifacts → green (strength from confidence)

### Synthesis
Final Claude assessment combining all prior evidence. Categories:
- PFC_GHOST → strong/moderate red
- AI_GENERATED → strong/moderate red
- LEGITIMATE → strong/moderate green
- INCONCLUSIVE → weak neutral

### Batching
Bio analysis and synthesis are batched (8 artists per Claude call). Image analysis is individual (images are large/unique). Fallback to individual calls on batch parse failure.

### Deep Evidence Integration
Deep evidence is merged into the existing evaluation via `incorporate_deep_evidence()`, which re-runs the decision tree with the expanded evidence set.

---

## 10. Playlist-Level Aggregation

### Health Score
Each artist's verdict is mapped to a health value:
```
Verified Artist:    100
Likely Authentic:    85
Inconclusive:        50
Suspicious:          25
Likely Artificial:    0
```
Playlist health = average of all artist health values.

### Report Sorting
Artists sorted by verdict severity (most concerning first), then by score within verdict tier.

---

## 11. Category Scores (Radar Chart)

Six dimensions scored 0-100 for visualization:

### Platform Presence
`platform_count × 14.3` (7 platforms max → 100)

### Fan Engagement
Additive from:
- Deezer fans: ≥1M (+50), ≥100K (+40), ≥10K (+25), ≥1K (+15), >0 (+5)
- Last.fm play/listener ratio: ≥10 (+20), ≥4 (+10), else ≥100 listeners (+5)
- Genius followers: ≥1K (+20), ≥100 (+10)
- Genius songs: ≥20 (+10), ≥5 (+5)

### Creative History
Additive from green flags:
- "albums in catalog" → +25; "album(s)" → +15
- Physical releases: strong → +30, else → +15
- Genius songs: strong → +20, moderate → +10, else → +5
- Collaborators → +10
Minus from red flags:
- "content farm" → -30
- "empty catalog" → -20

### Live Performance
- Setlist.fm shows: ≥50 (+40), ≥10 (+25), ≥1 (+10)
- Tour names → +15
- Countries: ≥5 (+25), ≥2 (+15), ≥1 (+5)

### Online Identity
- Social media count × 5 (capped at 8)
- Wikipedia → +20
- Discogs bio ≥200 chars → +15, ≥50 → +8
- Real name → +10
- Group members → +10
- Genius verified → +15

### Industry Signals
- ISNI → +30
- IPI → +30
- MusicBrainz metadata richness × 5
- Discogs quality "Correct" → +10
- PFC label match → -40

---

## 12. Blocklists

Three JSON files in `spotify_audit/blocklists/`:

| Blocklist | Used By | Match Effect |
|-----------|---------|-------------|
| `pfc_distributors.json` | Label evidence, Discogs evidence, MusicBrainz evidence, Standard label_blocklist | Strong red flag |
| `known_ai_artists.json` | Name evidence, Quick name_pattern | Strong red flag / score 100 |
| `pfc_songwriters.json` | Label evidence (contributor check), Credit network evidence | Strong red flag |

All three are checked during label evidence collection. Contributors are checked against `pfc_songwriters`.

---

## 13. Entity Database Intelligence

An optional SQLite database accumulates intelligence from prior scans. When available, the evidence collector checks:

1. **Artist status**: If previously flagged as `confirmed_bad` → strong red; `suspected` → moderate red; `cleared` → moderate green
2. **Label status**: Labels checked against entity DB; `confirmed_bad` labels → strong red; `suspected` → moderate red
3. **Songwriter status**: Contributors checked; `confirmed_bad` → strong red; `suspected` → moderate red
4. **Cowriter network**: If artist shares producers with ≥3 flagged artists → strong/moderate red; 1-2 → weak red

---

## 14. Open Questions & Known Issues

### Fixed Issues

5. **~~Rule 1 operator precedence bug~~** — FIXED. Added parentheses so the `or` and `and` bind correctly:
   ```python
   if ("known AI artist" in r.finding.lower() or "blocklist" in r.finding.lower()) and r.strength == "strong":
   ```

6. **~~Threat category legacy fallback inversion~~** — FIXED. Inverted the gate: now `if report.final_score >= 55: return None` (skip categories for legitimate artists). The threshold checks inside were also flipped to use `<=` for legitimacy scale (e.g., `report.final_score <= 35` for PFC Ghost).

10/4. **~~Rule 4 ignoring moderate reds~~** — FIXED. Rule 4 now requires `len(moderate_reds) <= len(strong_greens)` so 2 strong greens can't override 10 moderate reds. Rule 5 now caps moderate reds at 3.

11. **~~Last.fm play/listener ratio gap~~** — FIXED. Added two new tiers:
    - ratio 4-10 → moderate green ("reasonable replay rate")
    - ratio 2-4 → weak neutral ("borderline")

14. **~~Fragile string matching in Creative History category score~~** — FIXED. Refactored `compute_category_scores()` Creative History section to use `e.source` field (Deezer, Discogs, Genius) combined with broad keyword checks, instead of matching exact phrasings like `"albums in catalog"`.

### Remaining Design Questions

1. **Dual scoring paths**: The system maintains both legacy weighted scores and the evidence pipeline. Is the legacy path still needed? It adds complexity and can produce different results than the evidence path. The evidence system is strictly more expressive.

2. **Escalation uses old suspicion scale**: The escalation thresholds (`ESCALATE_TO_STANDARD = 30`, `ESCALATE_TO_DEEP = 50`) use the old suspicion score. Since the evidence pipeline runs independently, should escalation be based on evidence verdicts instead?

3. **Verdict-to-score mapping**: The `_verdict_to_score()` function converts a rich verdict back into a number. The confidence-weighted position within a range means a "Likely Authentic" with low confidence (score ~59) is almost the same as an "Inconclusive" with high confidence (score ~51). Is this the right behavior? Should confidence push scores across verdict boundaries?

7. **Weight balance**: Quick tier gives follower_listener_ratio and release_cadence each 15% weight, but image_quality and name_pattern only 5%. The evidence pipeline gives equal treatment to all signals via the flag strength system, making the weighted scores potentially disagree with the evidence verdict.

8. **"Not configured" as neutral**: When an API isn't configured, Standard signals default to 50 (neutral). This means unconfigured APIs don't affect the score at all. But the evidence pipeline generates red flags for "not found" which does affect the verdict. Should missing data be neutral or negative?

9. **Deezer fans threshold**: Rule 5 in the decision tree uses ≥50K Deezer fans as "Verified." This seems high — many legitimate indie/niche artists have < 50K Deezer fans. Is this threshold right?

### Remaining Data Observations

12. **Mood word detection**: The mood word list is hardcoded with ~50 English words. This is English-centric and might false-positive on ambient/electronic artists who are legitimate.

13. **Release cadence doesn't account for artist age**: A 20-year career with 200 releases = 0.83/month (green). A 6-month career with 20 releases = 3.3/month (moderate red). But both could be legitimate in their context.

15. **Discogs bio career keywords**: The keyword list includes "Grammy" and "festival" but not other common career markers like "producer", "songwriter", "engineer", "remixer", etc.
