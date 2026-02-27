# Artist Card UI Redesign Spec

## Overview

Redesign the artist detail card to use 6 evidence categories (down from 7 — removing "Online Identity"). Each category displays its score (0–100) and expandable evidence rows with green checkmarks or red flags. The card header includes a radar chart plus a summary metrics area with richer data.

---

## Card Header Layout

### Left Side: Radar Chart
Keep the existing hexagonal radar chart with 6 axes corresponding to the 6 categories below. Each axis scored 0–100.

### Right Side: Summary Metrics Area

Replace the current single-line summary with a richer layout:

**Row 1: Verdict + Confidence**
- Score badge (number), verdict label ("Likely Authentic" / "Suspicious" / etc.), confidence tag ("high confidence" / "moderate" / "low")

**Row 2: Platform Icons Row**
Show small icon/badge for each platform where the artist was found. Each badge should show the platform name and a checkmark (found) or X (not found). Platforms to include:
- Deezer
- MusicBrainz
- Genius
- Last.fm
- Discogs
- Setlist.fm
- YouTube
- Wikipedia

This gives an instant visual read on cross-platform presence without needing to expand any category.

**Row 3: Key Stats (3–4 small stat boxes)**
- **Last.fm Scrobbles** — e.g., "52.8M scrobbles"
- **Deezer Fans** — e.g., "312K fans"  
- **Concerts** — e.g., "187 shows"
- **Releases** — e.g., "6 albums, 18 singles"

These are the highest-signal quick-read numbers. Keep them compact.

---

## Category 1: PLATFORM PRESENCE

**What it answers:** Where does this artist exist across the music ecosystem?

| Data Point | Source | Signal | Notes |
|---|---|---|---|
| Deezer fan count | Deezer | Green if ≥10K fans | Primary cross-platform fan metric |
| YouTube channel + subscribers | YouTube | Green if channel exists with subs | Independent platform verification |
| Wikipedia article (byte count) | Wikipedia | Green if exists; byte count shows depth | Strong notability signal |
| Instagram followers | MusicBrainz URLs / scrape | Green if verified account exists | Social presence |
| Twitter/X followers | MusicBrainz URLs / scrape | Green if account exists | Social presence |
| Facebook page | MusicBrainz URLs | Green if exists | Social presence |
| Genius followers | Genius | Green if ≥100 followers | Lower priority but still platform signal |

**Display format:** Each row shows checkmark/X, the data point with value, and the source tag.

---

## Category 2: FAN ENGAGEMENT

**What it answers:** Do real humans listen to and engage with this artist?

| Data Point | Source | Signal | Notes |
|---|---|---|---|
| Last.fm listeners | Last.fm | Green if ≥10K | Core engagement metric |
| Last.fm scrobbles | Last.fm | Green if substantial | Volume of actual plays |
| Play/listener ratio | Last.fm | Green if ≥10 (strong engagement) | Key ratio — high means repeat listeners |
| Deezer fan count | Deezer | Green if ≥10K | Cross-platform fan verification |

**Display format:** Show the raw numbers and the computed ratio. The play/listener ratio is the star metric here — display it prominently.

---

## Category 3: CREATIVE HISTORY

**What it answers:** Does this artist have a legitimate creative body of work?

| Data Point | Source | Signal | Notes |
|---|---|---|---|
| Albums / singles / EPs breakdown | Catalog | Green if ≥3 albums with normal cadence | Show counts explicitly |
| **Releases per year timeline** | Catalog | Green if steady; Red if >6/mo | **Key change: break down releases by year** — e.g., "2019: 1 album, 3 singles / 2020: 1 album, 2 singles / 2021: 4 singles" |
| Avg song duration + variation | Catalog | Green if ≥180s with ≥30s stdev | Red if <90s avg (stream farm) or <10s stdev (cookie-cutter) |
| Collaborative songwriting | Genius / MusicBrainz | Green if multiple collaborators | Shows real creative network |
| Number of groups/bands | MusicBrainz | Green if member of groups | Artist is part of multiple projects = depth |
| Collaborators / related artists | Deezer / MusicBrainz | Green if 3+ collabs, 5+ related | Credit network richness |

**Display format:** The per-year release timeline should be a compact inline visualization or table — something like:
```
2018: ██ 1 album, 2 singles
2019: ███ 1 album, 4 singles  
2020: █ 2 singles
2021: ██ 1 album, 3 singles
```

---

## Category 4: IRL PRESENCE

**What it answers:** Does this artist exist in the physical world?

| Data Point | Source | Signal | Notes |
|---|---|---|---|
| Concerts on Setlist.fm | Setlist.fm | Green if any; strong if 10+ | Count + countries |
| Physical releases on Discogs | Discogs | Green if vinyl/CD/cassette exists | Physical media = real investment |
| Upcoming shows | Bandsintown | Green if upcoming shows scheduled | Active touring = real artist |
| Countries performed in | Setlist.fm | Green if 2+ countries | Geographic spread |
| Named tours | Setlist.fm | Green if named tours exist | Tour branding = career depth |

---

## Category 5: INDUSTRY SIGNALS

**What it answers:** Is this artist recognized by the music industry infrastructure?

| Data Point | Source | Signal | Notes |
|---|---|---|---|
| MusicBrainz entry (type, country) | MusicBrainz | Green if exists with rich metadata | Type = Person/Group, country, begin date |
| Genius profile (song count) | Genius | Green if 20+ songs | Catalog depth on lyrics platform |
| ISNI registered | MusicBrainz | Green if has ISNI code | International naming authority |
| IPI registered | MusicBrainz | Green if has IPI code | Performing rights org registration |
| ASCAP registration | ASCAP/Songview | Green if registered as songwriter | PRO registration with ownership share |
| BMI registration | BMI Repertoire | Green if registered as songwriter | PRO registration with ownership share |
| Label / distributor name | Catalog / MusicBrainz | Neutral (display for context) | Shows who distributes the music |
| Discogs bio with career keywords | Discogs | Green if bio has career narrative | Real biographical depth |

**Note on ASCAP/BMI:** If the artist is found as a registered songwriter with an ownership share, that's a strong green. If found but with 0% songwriter share (100% publisher), that's actually a red flag indicating work-for-hire / PFC arrangement. If the publisher matches a known PFC entity, that's a strong red.

---

## Category 6: BLOCKLIST STATUS

**What it answers:** Does this artist match any known fraud databases?

| Data Point | Source | Signal | Notes |
|---|---|---|---|
| Known AI artist blocklist | known_ai_artists.json | Red if match; Green if clean | 2,600+ confirmed fake artists |
| PFC distributor match | pfc_distributors.json | Red if label/distributor matches | Epidemic Sound, Firefly, etc. |
| PFC songwriter match | pfc_songwriters.json | Red if credited writers match | Shared producer networks |
| Entity database prior flags | SQLite entity DB | Red if previously flagged | Accumulated intelligence from prior scans |
| Publisher ownership (ASCAP/BMI) | Songview | Red if publisher is known PFC entity | Cross-ref publisher names against blocklists |

**Display format:** This section should feel like a status dashboard. If everything is clean, show a prominent "Clean across all blocklists" green banner. If there are hits, show each match with the specific blocklist and matched entity name.

---

## Removed: Online Identity

The following data points from the old "Online Identity" category have been redistributed:
- YouTube → Platform Presence
- Wikipedia → Platform Presence  
- Social media (Instagram, Twitter) → Platform Presence
- Authentic artist bio → Industry Signals (Discogs bio)

---

## Styling Notes

- Keep the existing dark theme with green/red/amber color coding
- 4-tier signal strength: strong green, moderate green, moderate red, strong red
- Each category header shows the category icon, name, and score (0–100) on the right
- Categories are expandable/collapsible
- Evidence rows show: signal icon (✓/✗), finding text, source tag (e.g., "Last.fm", "Deezer")
- Radar chart axes map 1:1 to the 6 categories
