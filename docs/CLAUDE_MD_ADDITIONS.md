# Spotify Playlist Authenticity Analyzer

## Project Overview

This project detects Perfect Fit Content (PFC) — fake artists and commissioned tracks that Spotify places on editorial playlists. We have a corpus of **2,600 artists** from known PFC playlists and a 5-phase pipeline to analyze them, discover patterns, and build an evidence-based detection model.

## Reference Documents

Read these before building any detection or analysis logic:

- `docs/pfc_analysis_pipeline.md` — **START HERE.** The implementation guide for the 5-phase analysis pipeline. Contains directory structure, API query sequences, enriched profile schema, entity graph design, feature matrix spec, and session-by-session build plan.
- `docs/spotify-audit-spec.md` — Original build spec, CLI design, scoring system with 6-dimension radar chart
- `docs/api_field_research.md` — Exact API fields available from each of the 7 data sources (MusicBrainz, Deezer, Genius, Discogs, Setlist.fm, Last.fm, Bandsintown)
- `docs/bad_actor_database.md` — Known PFC providers, producers, labels, fake artist names. Use this for matching in Phase 2 entity expansion. Key entities: Epidemic Sound, Firefly Entertainment, Queenstreet/Audiowell, Johan Röhr, Christer Sandelin/"Chillmi"
- `docs/pfc_playlist_registry.md` — 76 documented PFC playlists with evidence tiers
- `docs/pfc_playlists_fake_artists_database.md` — Extended fake artist name database (the 2,600 seed artists)

## Pipeline Scripts

```
scripts/
  01_enrich.py          # Phase 1: Query 7 APIs per seed artist → data/enriched/
  02_expand.py          # Phase 2: Extract entities, build graph → data/entities/
  03_mine.py            # Phase 3: Statistical pattern analysis → data/patterns/
  04_validate.py        # Phase 4: Feature engineering + validation → data/features/
  05_train.py           # Phase 5: Model training → data/model/
  utils/
    api_clients.py      # Rate-limited wrappers for all 7 APIs
    rate_limiter.py     # Adaptive per-API rate limiter with exponential backoff
    name_analyzer.py    # NLP utilities for naming convention analysis
    entity_extractor.py # Parse entities from raw API responses
```

## Data Directory

```
data/
  seeds/                # Input: artist names + playlist sources
  enriched/             # Phase 1 output: one JSON per artist with all API data
  entities/             # Phase 2 output: producer/label/distributor graphs
  patterns/             # Phase 3 output: naming, temporal, platform analysis
  features/             # Phase 4 output: feature matrix + signal importance
  model/                # Phase 5 output: trained classifier + validation report
```

## Critical Implementation Rules

1. **All scripts must be RESUMABLE.** Check for existing output files before making API calls. Every script can be interrupted and restarted without data loss.
2. **Never discard raw API responses.** Store raw alongside normalized data in every enriched profile. We may discover new signals later that require re-parsing.
3. **One artist at a time.** Process all 7 APIs for one artist before moving to the next. Save after each artist completes.
4. **Log everything.** Every API call gets timestamp, HTTP status, response time. Every disambiguation decision gets logged with confidence level.
5. **Exponential backoff on rate limits.** Start at 1 second, double each retry, max 60 seconds. Per-API rate limits vary — see the rate limits table in `docs/pfc_analysis_pipeline.md`.
6. **Error handling: never crash on a single failure.** 404 = record as `"found": false` (absence is data). 500 = retry 3x then record as error. Timeout = retry 3x then record as timeout.

## API Keys

Read from `.env` file in project root:

```
LASTFM_API_KEY=...
GENIUS_TOKEN=...
DISCOGS_TOKEN=...
SETLIST_FM_API_KEY=...
BANDSINTOWN_APP_ID=...
```

MusicBrainz requires a descriptive `User-Agent` header, no API key. Deezer requires no auth.

## Rate Limits Quick Reference

| API | Limit | Auth |
|-----|-------|------|
| MusicBrainz | 1 req/sec | User-Agent header |
| Deezer | 50 req/5sec | None |
| Genius | ~5 req/sec | Bearer token |
| Discogs | 60 req/min | Personal token |
| Setlist.fm | 2 req/sec | API key in header |
| Last.fm | 5 req/sec | API key as param |
| Bandsintown | 1 req/sec | App ID as param |

## Key Concepts

- **PFC (Perfect Fit Content)**: Spotify's internal term for commissioned music created by production companies under fake artist names, placed on editorial playlists to reduce royalty payments to real musicians.
- **Ghost Artist**: A fabricated artist identity with no real person behind it. Created by PFC production companies.
- **Shell Label**: A record label that exists only to distribute PFC content. Has no real artist roster outside the PFC corpus.
- **Entity Graph**: The knowledge graph built in Phase 2 mapping producers → artists, labels → artists, distributors → artists. High-connectivity nodes are PFC network indicators.
- **Control Group**: 200-300 known-legitimate artists from similar genres (ambient, lo-fi, jazz, piano), enriched through the same pipeline. Required for Phase 4 statistical validation.

## What NOT To Do

- Do NOT use the Spotify API — we don't have access and the project scope explicitly excludes it.
- Do NOT hardcode API keys in scripts — always read from `.env`.
- Do NOT process all artists for one API then move to the next — process all APIs per artist.
- Do NOT skip the control group — Phase 4 validation is meaningless without a legitimate comparison set.
- Do NOT treat "not found" as an error — platform absence is one of our strongest detection signals.
- Do NOT assume all 2,600 artists are fake — some legitimate indie artists appear on PFC playlists. The pipeline accounts for this with the LIKELY_LEGIT label category.
