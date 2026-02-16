# Spotify Auditor - Development Context

## Architecture
- **Package**: `spotify_audit/` with submodules `analyzers/`, `reports/`, `blocklists/`, `data/`
- **CLI entry**: `spotify_audit/cli.py` (Click) → installed as `spotify-audit`
- **3 analysis tiers**: Quick (Spotify/Deezer only), Standard (+ external APIs), Deep (+ Claude AI)
- **Primary scoring**: Evidence-based decision tree (`evidence.py`) → Verdicts with red/green flags
- **Supplementary**: Legacy weighted 0-100 scores from quick/standard/deep tiers (inverted to legitimacy scale)
- **Cache**: SQLite with 7-day TTL in `spotify_audit/data/cache.db`
- **Reports**: Markdown, HTML, JSON via `reports/formatter.py`

## Key Files
| File | Purpose |
|------|---------|
| `evidence.py` | Decision tree: ~18 evidence collectors, ExternalData (55 fields) → Verdict + explanation |
| `scoring.py` | ArtistReport, `_verdict_to_score()` (legitimacy 0-100), threat category inference |
| `config.py` | Weights, blocklist loaders, SCORE_LABELS, escalation thresholds |
| `cli.py` | Click CLI, workflow orchestration, concurrent API lookups, entity DB auto-populate |
| `entity_db.py` | SQLite relational DB: artists/labels/songwriters/publishers + relationships |
| `entity_cli.py` | Click CLI for entity DB (import, query, flag, network, stats) |
| `deep_analysis.py` | Claude bio + image + synthesis, batch mode (8 artists/call) |
| `spotify_client.py` | SpotifyScraper wrapper with retry/backoff, ArtistInfo dataclass |
| `deezer_client.py` | Deezer API (free, no auth), DeezerArtist with full enrichment |
| `genius_client.py` | Genius API: 15-result search, 2-pass matching (exact + partial) |
| `setlistfm_client.py` | Setlist.fm API: 2-pass matching, concert history |
| `lastfm_client.py` | Last.fm API (listeners, playcount, bio, tags, similar, top tracks) |
| `musicbrainz_client.py` | MusicBrainz API (type, country, dates, ISNIs, IPIs, URLs) |
| `discogs_client.py` | Discogs API (physical releases, labels, bio, members) |
| `blocklist_builder.py` | Analyzes scan data to suggest blocklist additions |

## Scoring System (v0.6 — legitimacy scale)
Higher score = more legitimate. Derived from evidence verdict + confidence + flag balance.

| Score Range | Label | Verdict |
|-------------|-------|---------|
| 80-100 | Verified Artist | Strong green flags, multi-platform, large fanbase |
| 55-79 | Likely Authentic | More green than red, some platform presence |
| 35-54 | Inconclusive | Mixed or insufficient evidence |
| 15-34 | Suspicious | More red than green flags |
| 0-14 | Likely Artificial | PFC label match, content farm patterns, no presence |

`_verdict_to_score()` in `scoring.py` blends verdict range + confidence (70%) + green/red flag balance (30%).

## Evidence Pipeline
1. **Platform evidence**: Spotify, Deezer, MusicBrainz, Genius, Discogs, Setlist.fm, Last.fm (6 external platforms)
2. **Core collectors**: followers, catalog, durations, releases (singles vs albums/month), labels, names, collabs, genres, ranks
3. **External collectors**: genius credits, discogs physical/bio, live shows, musicbrainz metadata, social media, identity, Last.fm engagement, touring geography
4. **Entity DB**: Prior intelligence from accumulated scans
5. **Decision tree**: Weighted flag counting → Verdict + confidence

## API Clients
- **Always available** (no key): Deezer, MusicBrainz
- **Requires free API key**: Genius (`GENIUS_TOKEN`), Discogs (`DISCOGS_TOKEN`), Setlist.fm (`SETLISTFM_API_KEY`), Last.fm (`LASTFM_API_KEY`)
- **Requires paid key**: Claude/Anthropic (`ANTHROPIC_API_KEY`) — Deep tier only

## Blocklists
JSON files in `spotify_audit/blocklists/`:
- `pfc_distributors.json` — PFC-associated labels/distributors
- `known_ai_artists.json` — Confirmed AI-generated artist names
- `pfc_songwriters.json` — PFC-associated songwriters

Label evidence checks ALL three lists. Contributors checked against `pfc_songwriters`.

## Pipeline Scripts (scripts/)
- `01_enrich.py` — Phase 1: 6-API enrichment per artist → `data/enriched/`
- `02_expand.py` — Phase 2: Entity graph expansion
- `03_mine.py` — Phase 3: Pattern mining
- `04_validate.py` — Phase 4: Statistical validation vs control group
- `05_train.py` — Phase 5: ML classifier training
- `utils/rate_limiter.py` — Adaptive per-API rate limiter

## Recent Changes (v0.6)
- Genius/Setlist.fm: 2-pass matching (exact + partial), WARNING-level logging
- Bandsintown client removed (API defunct, never returned data)
- Release pace: separate singles/month vs albums/month thresholds
- Scores flipped to legitimacy scale (Verified >80, PFC <14)
- Label checking expanded: PFC distributors + known AI + PFC songwriters
- Discogs bio: career keyword + year reference analysis
- MusicBrainz social links in evidence card (FB/IG/X/YT/BC/SC)
- Last.fm play/listener ratio in data summary
- Removed weak signals: similar artists count, moderate scrobble ratio

## Build Notes
- Python 3.11+, uses `setuptools.build_meta` (not `_legacy:_Backend`)
- `Wikipedia-API` fails to build — moved to optional `[standard]` extra
- Install: `pip install -e .` from repo root
- Run: `spotify-audit <playlist-url> --tier standard`
