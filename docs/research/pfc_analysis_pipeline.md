# PFC Artist Corpus Analysis Pipeline

> **Purpose**: Use 2,600 artists from known PFC playlists as seeds to discover patterns, entities, and signals of non-authentic music. This document is the implementation guide for Claude Code.
>
> **Input**: `data/seeds/artist_seeds.json` — 2,600 artist names with source playlist info
> **Output**: Entity graph, validated detection signals, trained classifier, updated scoring weights

---

## Pipeline Overview

```
artist_seeds.json (2,600 artists)
        │
        ▼
  Phase 1: ENRICH ── Query 7 APIs per artist ──▶ data/enriched/
        │
        ▼
  Phase 2: EXPAND ── Extract entities, build graph ──▶ data/entities/
        │
        ▼
  Phase 3: MINE ──── Statistical pattern analysis ──▶ data/patterns/
        │
        ▼
  Phase 4: VALIDATE ─ Feature engineering + stats ──▶ data/features/
        │
        ▼
  Phase 5: MODEL ─── Classifier training ──▶ data/model/
```

---

## Directory Structure

```
data/
  seeds/
    artist_seeds.json          # 2,600 artist names + spotify IDs + source playlists
    control_group.json         # 200-300 known-legitimate artists (built in Phase 3)
  enriched/
    {artist_id}.json           # One file per artist, all API data
    _progress.json             # Enrichment progress tracker
    _errors.json               # Failed lookups for retry
  entities/
    producers.json             # Producer/songwriter → [artists] with corpus %
    labels.json                # Label → [artists] with exclusivity scores
    distributors.json          # Distributor → [artists] from ISRC analysis
    cowriters.json             # Co-writer network edges
    similar_artists.json       # Similar-artist overlap with corpus
    new_bad_actors.json        # Entities NOT in bad_actor_database.md but flagged
  patterns/
    naming_analysis.json       # NLP on artist names
    temporal_patterns.json     # Release cadence, day-of-week, burst detection
    platform_profiles.json     # Cross-platform binary presence matrix + clusters
    label_clusters.json        # Label co-occurrence and concentration
  features/
    feature_matrix.csv         # All artists × all features, model-ready
    signal_importance.json     # Ranked features with p-values and effect sizes
    revised_scoring_weights.json
  model/
    classifier.pkl
    validation_report.json

scripts/
  01_enrich.py
  02_expand.py
  03_mine.py
  04_validate.py
  05_train.py
  utils/
    api_clients.py             # Rate-limited wrappers for all 7 APIs
    rate_limiter.py            # Adaptive per-API rate limiter with backoff
    name_analyzer.py           # NLP for naming convention analysis
    entity_extractor.py        # Parse entities from raw API responses
```

---

## Phase 1: Data Collection & Entity Enrichment

**Goal**: Build complete multi-platform profiles for all 2,600 artists.
**Runtime**: ~7 hours at conservative rate limits.
**Script**: `01_enrich.py`

### Per-Artist Query Sequence

Process all 7 APIs for one artist before moving to the next. Order matters — earlier results inform later queries.

| # | API | Query Strategy | Key Fields to Extract | PFC Detection Value |
|---|-----|---------------|----------------------|-------------------|
| 1 | **MusicBrainz** | Search by name. If multiple results, use disambiguation + type=person/group. Extract MBID for downstream lookups. | MBID, type, area, begin-area, begin/end dates, ISNIs, IPIs, URLs, tags, release-groups, relations (writer/producer) | Absence = strong PFC signal. ISNI/IPI = definitive real identity proof. Relations reveal producer network. |
| 2 | **Deezer** | Search `/search/artist?q={name}`. Then GET `/artist/{id}` and `/artist/{id}/albums`. | deezer_id, nb_fan, nb_album, albums[].title, albums[].label, albums[].release_date, albums[].contributors | nb_fan is the best free engagement metric. Label names feed the entity graph. Contributors reveal production credits. |
| 3 | **Genius** | Search `/search?q={name}`. Get artist page. Then `/artists/{id}/songs` for credits. Paginate to get all songs. | genius_id, followers_count, song_count, songs[].writer_artists, songs[].producer_artists, songs[].featured_artists | **CRITICAL for Phase 2.** Songwriter/producer credits are the backbone of the entity graph. This is how Johan Röhr was identified — same 3-5 writers across hundreds of "different" artists. |
| 4 | **Discogs** | Search `/database/search?q={name}&type=artist`. If found, GET `/artists/{id}/releases`. | discogs_id, profile, realname, aliases, members, releases[].format, releases[].label, releases[].year, releases[].country | Physical releases (format=Vinyl or CD) are near-impossible to fake. Strongest single legitimacy signal. |
| 5 | **Setlist.fm** | Search `/rest/1.0/search/artists?artistName={name}`. Use MBID from step 1 when available. Get `/artist/{mbid}/setlists`. | setlist_count, setlists[].venue, setlists[].eventDate, tour names, countries | Live performance = near-definitive legitimacy. 0 setlists + millions of streams = highly suspicious. |
| 6 | **Last.fm** | GET `artist.getinfo` + `artist.gettoptracks`. | listeners, playcount, bio, similar_artists, tags, top_tracks[].listeners | Listener-to-playcount ratio is a fraud signal. Bio presence = community knowledge. Similar artists reveal clustering. |
| 7 | **Bandsintown** | GET `/artists/{name}?app_id={key}`. Then `/artists/{name}/events`. | tracker_count, upcoming_event_count, events[].venue, events[].datetime, support_url, links | Tracker count = fan engagement. Events corroborate Setlist.fm. Links = web presence verification. |

### Rate Limits Reference

| API | Rate Limit | Calls/Artist | Time for 2,600 | Auth |
|-----|-----------|-------------|----------------|------|
| MusicBrainz | 1 req/sec | ~2 | ~87 min | User-Agent header only |
| Deezer | 50 req/5sec | ~3 | ~13 min | None needed |
| Genius | ~5 req/sec | ~3 (+ pagination) | ~26 min | Access token |
| Discogs | 60 req/min (auth) | ~2 | ~87 min | Personal token |
| Setlist.fm | 2 req/sec | ~2 | ~43 min | API key |
| Last.fm | 5 req/sec | ~2 | ~17 min | API key |
| Bandsintown | 1 req/sec | ~2 | ~87 min | App ID |

**Total: ~360 min of API time. Cost: $0.**

### Enriched Profile Schema

```json
{
  "artist_name": "Elara Voss",
  "artist_id": "normalized_slug_or_hash",
  "seed_source": {
    "playlist": "Deep Focus",
    "playlist_id": "...",
    "position": 12
  },
  "enrichment_timestamp": "2026-02-08T...",
  "platforms_found": ["deezer", "lastfm"],
  "platforms_missing": ["musicbrainz", "discogs", "genius", "setlist_fm", "bandsintown"],
  "platform_count": 2,

  "musicbrainz": {
    "found": false,
    "disambiguation_confidence": "n/a",
    "raw": null
  },
  "deezer": {
    "found": true,
    "id": 12345678,
    "nb_fan": 234,
    "nb_album": 3,
    "albums": [
      {
        "title": "Ambient Drift",
        "label": "Firefly Recordings",
        "release_date": "2022-03-15",
        "tracks": 8,
        "contributors": ["Producer Name"]
      }
    ],
    "raw": {}
  },
  "genius": { "found": false, "raw": null },
  "discogs": { "found": false, "raw": null },
  "setlist_fm": { "found": false, "raw": null },
  "lastfm": {
    "found": true,
    "listeners": 1200,
    "playcount": 89000,
    "listener_play_ratio": 74.2,
    "bio_exists": false,
    "similar_artists": ["Other PFC Name 1", "Other PFC Name 2"],
    "tags": ["ambient", "chill"],
    "raw": {}
  },
  "bandsintown": { "found": false, "raw": null },

  "extracted_entities": {
    "labels": ["Firefly Recordings"],
    "producers": [],
    "distributors": [],
    "cowriters": [],
    "similar_in_corpus": ["Other PFC Name 1", "Other PFC Name 2"]
  }
}
```

### Name Disambiguation Strategy

Many PFC names are deliberately generic ("Luna", "Aura", "Drift"). APIs will return multiple results.

1. **Single result** → Accept
2. **Multiple results** → Prefer exact name match over partial
3. **Multiple exact matches** → Check genre overlap with source playlist (e.g., artist from "Peaceful Piano" → prefer ambient/piano/classical result)
4. **Still ambiguous** → Flag as `"disambiguation_confidence": "ambiguous"`, store all candidates
5. **Log confidence for every lookup** — `high/medium/low/ambiguous`. This is itself a feature: PFC names being hard to disambiguate is a signal.

### Implementation Requirements for 01_enrich.py

- **Resumable**: Check if `data/enriched/{artist_id}.json` exists with all 7 platforms before querying. Allow restart without data loss.
- **Per-API rate limiter**: Different limits per API. Use `utils/rate_limiter.py` with adaptive backoff.
- **Error handling**: 429 → exponential backoff (1s, 2s, 4s... max 60s). 404 → record as `"found": false` (this IS data). 500 → retry 3x then record as `"status": "error"`. Timeout → retry 3x then `"status": "timeout"`. **Never crash on a single artist failure.**
- **Progress logging**: Print every 10 artists: `[142/2600] Enriched 'Kael Sundrift' — found on: deezer, lastfm (5 missing)`. Save `_progress.json`.
- **Batch checkpoint**: Save after every artist. If script dies mid-run, only lose current artist.
- **API keys**: Read from `.env` file: `LASTFM_API_KEY`, `GENIUS_TOKEN`, `DISCOGS_TOKEN`, `SETLIST_FM_API_KEY`, `BANDSINTOWN_APP_ID`. MusicBrainz and Deezer are keyless (MB needs User-Agent).

---

## Phase 2: Entity Expansion — Building the Graph

**Goal**: Extract every related entity from enriched profiles and build a knowledge graph. 2,600 data points → potentially 10,000+ connected entities.
**Script**: `02_expand.py`

### Entity Types to Extract

| Entity Type | Source APIs | How to Extract | Why It Matters |
|------------|-----------|---------------|---------------|
| **Songwriters / Producers** | Genius (primary), MusicBrainz relations | Parse `writer_artists` and `producer_artists` from Genius song credits. Extract "writer" and "producer" relations from MB. | **THE most important entity type.** If producer X wrote for 50+ PFC artists, X is a PFC network node. |
| **Labels** | Deezer albums, MusicBrainz releases, Discogs releases | Normalize names: trim whitespace, lowercase, remove trailing "Records"/"Music"/"Entertainment" for fuzzy matching. | Shell labels are a primary PFC vehicle. A label appearing 100+ times in corpus but nowhere else = confirmed PFC entity. |
| **Distributors** | ISRC registrant codes (from MB recordings), Deezer © and ℗ lines | Parse ISRC prefix (first 5 chars = registrant code). Parse copyright/phonogram lines from release metadata. | Supply chain bottleneck. If 80% of corpus routes through 3 distributors, that's actionable intelligence. |
| **Co-writers / Collaborators** | Genius featured artists, MusicBrainz artist relations | Build edges: if Artist A and Artist B share a songwriter, that's a weighted connection. | PFC artists sharing multiple co-writers form clusters. Clusters = production operations. |
| **Publishing Entities** | MusicBrainz work relations, Genius metadata | Extract publishing company names where available. | Epidemic Sound Publishing, Queenstreet Music Publishing = direct PFC identifiers. |
| **Similar Artists** | Last.fm similar artists, Deezer related artists | Collect similar artists lists, check overlap with seed corpus. | If 80% of an artist's "similar" list is other PFC artists → algorithmic clustering signal. |

### Entity Graph Schema

Each entity file maps entity → connected artists with metadata:

```json
// entities/producers.json
{
  "Johan Röhr": {
    "artist_count": 147,
    "artists": ["Elara Voss", "Kael Sundrift", "Maya Åström"],
    "pfc_corpus_percentage": 0.057,
    "known_bad_actor": true,
    "bad_actor_db_match": "bad_actor_database.md#johan-rohr",
    "sources": ["genius_credits", "musicbrainz_relations"],
    "first_seen_date": "2017-03-12"
  },
  "Unknown Producer X": {
    "artist_count": 38,
    "artists": ["..."],
    "pfc_corpus_percentage": 0.015,
    "known_bad_actor": false,
    "investigation_flag": true,
    "sources": ["genius_credits"]
  }
}

// entities/labels.json
{
  "Firefly Recordings": {
    "artist_count": 89,
    "artists": ["..."],
    "pfc_corpus_percentage": 0.034,
    "known_bad_actor": true,
    "exclusivity_score": null,
    "total_roster_size": null
  }
}
```

### Investigating High-Connectivity Nodes

**Trigger**: Any entity connected to **5+ PFC artists** gets flagged for deeper investigation.

**For flagged producers:**
- Search Genius for their FULL songwriting catalog (not just songs connected to our corpus)
- If they wrote for 200 artists and 50 are in our corpus → 25% PFC rate → damning
- A legitimate producer might have 1-2% overlap by coincidence
- Store full catalog data in `entities/producer_investigations/{name}.json`

**For flagged labels:**
- Search MusicBrainz + Discogs for ALL releases on that label
- Compute `exclusivity_score = pfc_artists_on_label / total_artists_on_label`
- Score of 0.8+ = almost certainly a shell label
- Store in `entities/label_investigations/{name}.json`

**For flagged distributors:**
- Cross-reference ISRC registrant code against known PFC distributor codes from `bad_actor_database.md`
- Check legitimate artist overlap (DistroKid serves both real and fake)
- Distributor alone is not diagnostic — distributor + other signals is powerful

**Key deliverable**: `entities/new_bad_actors.json` — entities discovered through graph analysis that are NOT in our existing bad actor database. These are **new findings**.

---

## Phase 3: Pattern Mining & Signal Discovery

**Goal**: Statistical analysis across the full corpus to discover patterns that distinguish PFC from authentic music. These become new detection signals.
**Script**: `03_mine.py`

### 3.1 Naming Convention Analysis → `patterns/naming_analysis.json`

| Analysis | Method | Expected Finding |
|---------|--------|-----------------|
| Name length distribution | Histogram of character counts | PFC clusters around 10-18 chars (two-word names). Real artists have wider variance. |
| Word count distribution | Count words per name | PFC overwhelmingly 2-word (FirstName LastName). Real artists include 1-word, 3+, "The X" at higher rates. |
| Cultural-linguistic consistency | Name-origin lookup/classifier on first vs last name | Swedish producers generate names from multiple cultures. Nordic first + Latin last = suspicious mismatch. |
| Phonemic analysis | Extract consonant/vowel patterns | PFC may prefer "soft" phonemes (l, r, s, v, n) matching ambient/relaxation aesthetics. |
| Name collision rate | Exact-match search across corpus and MusicBrainz | High collision may be intentional (harder to find the specific artist). |

### 3.2 Release Pattern Analysis → `patterns/temporal_patterns.json`

| Analysis | Method | Expected Finding |
|---------|--------|-----------------|
| Release cadence | Days between releases per artist, K-means clustering | Industrial ops likely release on fixed 7/14/28-day schedules. Real artists are irregular. |
| Day-of-week distribution | Which day releases land on | PFC may cluster on Spotify editorial refresh days (Fridays). |
| Catalog shape | `singles_ratio = singles / (singles + albums + EPs)` | PFC = overwhelmingly singles. Real artists have more albums/EPs. |
| Track duration clustering | Duration from Deezer album data | PFC may cluster around payout-optimized durations (2-3 min ambient). |
| Career lifespan | `last_release_date - first_release_date` | PFC may show short or suspiciously fixed-length lifespans. Real artists = messy, long. |
| Burst detection | Flag artists with 10+ tracks in one month then silence | Matches commissioned batch production model. |

### 3.3 Cross-Platform Presence Matrix → `patterns/platform_profiles.json`

Build a binary matrix: rows = artists, columns = 7 platforms. Each cell = 1 (found) or 0 (not found).

**Analyses:**
- **Platform fingerprinting**: What's the most common presence pattern in PFC corpus? Hypothesis: `[Deezer=1, Last.fm=1, everything else=0]` because Deezer auto-ingests from distributors and Last.fm passively scrobbles.
- **Absence scoring**: Which platform absences are most diagnostic? Hypothesis: Discogs absence is strongest, but data may show Setlist.fm or Genius absence is more discriminating.
- **Cluster analysis**: K-means or hierarchical clustering on the binary matrix. Do natural clusters emerge? Do they correspond to threat categories?

### 3.4 Label Network Analysis → `patterns/label_clusters.json`

- **Label concentration**: What % of corpus is covered by top 10 labels? If 10 labels = 60% of 2,600 artists → extreme industrial concentration.
- **Label exclusivity**: For each label, compute `pfc_artists / total_artists`. Requires additional MusicBrainz/Discogs queries for non-PFC artists on those labels.
- **Bad actor DB cross-reference**: Auto-flag matches against `bad_actor_database.md`. Separately flag new suspicious labels.
- **Label co-occurrence**: Do certain labels appear together on the same playlists? Could reveal corporate parent relationships.

### 3.5 Control Group (CRITICAL)

**You must build a control group before Phase 4 validation can work.**

Collect 200-300 **known-legitimate** artists from the same genres (ambient, lo-fi, jazz, piano, chill) and run them through the same Phase 1 enrichment pipeline. Sources for legitimate artists:
- Bandcamp best-sellers in ambient/electronic
- Artists with verified Wikipedia pages + physical discographies
- Artists who have played major festivals (Primavera, SXSW, Pitchfork)
- Artists with 10+ years of documented career history

Store in `data/seeds/control_group.json` and enrich into `data/enriched/` with a `"control_group": true` flag.

---

## Phase 4: Statistical Validation & Feature Engineering

**Goal**: Convert Phase 3 discoveries into quantified features with measured discriminative power.
**Script**: `04_validate.py`

### Feature Matrix (`features/feature_matrix.csv`)

One row per artist. All features numeric or binary.

| Feature | Type | Construction |
|---------|------|-------------|
| `platform_count` | int 0-7 | Count of platforms where artist was found |
| `has_musicbrainz` | binary | 1 if found on MusicBrainz |
| `has_discogs` | binary | 1 if found on Discogs |
| `has_discogs_physical` | binary | 1 if any Discogs release has format=Vinyl or CD |
| `has_genius` | binary | 1 if found on Genius |
| `has_setlists` | binary | 1 if any setlists on Setlist.fm |
| `has_bandsintown_events` | binary | 1 if any events on Bandsintown |
| `deezer_fan_count` | int | Raw nb_fan (log-transform for modeling) |
| `lastfm_listeners` | int | Last.fm listener count |
| `lastfm_listener_play_ratio` | float | playcount / listeners |
| `lastfm_bio_exists` | binary | 1 if Last.fm bio is present |
| `genius_songwriter_count` | int | Unique songwriters credited |
| `genius_song_count` | int | Total songs on Genius |
| `setlist_count` | int | Number of setlists |
| `setlist_country_count` | int | Unique countries performed in |
| `bandsintown_trackers` | int | Tracker count |
| `max_producer_corpus_pct` | float | Highest PFC corpus % among this artist's producers |
| `label_exclusivity_score` | float 0-1 | % of label's roster in PFC corpus |
| `known_bad_actor_label` | binary | 1 if label matches bad_actor_database.md |
| `known_bad_actor_producer` | binary | 1 if any producer matches bad_actor_database.md |
| `singles_ratio` | float 0-1 | singles / total_releases |
| `release_cadence_cv` | float | Coefficient of variation of days between releases (low = regular = suspicious) |
| `career_lifespan_days` | int | Days between first and last release |
| `has_burst_releases` | binary | 1 if 10+ releases in any single month |
| `avg_track_duration_sec` | float | Average track duration |
| `name_word_count` | int | Words in artist name |
| `name_char_count` | int | Characters in artist name |
| `name_cultural_mismatch` | binary | 1 if first/last name origins differ culturally |
| `similar_artist_corpus_overlap` | float 0-1 | % of similar artists that are also in PFC corpus |
| `disambiguation_confidence` | categorical | high/medium/low/ambiguous |
| `is_pfc_corpus` | binary | 1 = from PFC seeds, 0 = control group (target variable for modeling) |

### Signal Validation Against Control Group

For every feature, test PFC corpus vs control group:

- **Binary features**: Fisher's exact test → p-value + odds ratio. OR of 10 = PFC artists 10x more likely to have that flag.
- **Continuous features**: Mann-Whitney U test → p-value + effect size (rank-biserial correlation).
- **Multi-class features**: Kruskal-Wallis test.

**Output** (`features/signal_importance.json`):
```json
[
  {
    "feature": "has_discogs_physical",
    "test": "fisher_exact",
    "p_value": 0.00001,
    "odds_ratio": 47.3,
    "pfc_rate": 0.02,
    "control_rate": 0.89,
    "interpretation": "PFC artists almost never have physical releases. Strongest single signal."
  },
  {
    "feature": "platform_count",
    "test": "mann_whitney_u",
    "p_value": 0.00001,
    "effect_size": 0.82,
    "pfc_median": 2,
    "control_median": 6,
    "interpretation": "PFC artists found on far fewer platforms."
  }
]
```

### Updating the Scoring Model

Use Phase 4 results to:
1. **Validate or revise radar chart dimension weights** — if Live Performance is less discriminating than Label Network, adjust.
2. **Add new dimensions** — if naming conventions prove highly discriminating, add as its own axis.
3. **Remove weak signals** — if a feature shows no statistical difference (e.g., genre tags), remove to reduce noise.
4. **Output** → `features/revised_scoring_weights.json`

---

## Phase 5: Ground Truth Labeling & Model Training

**Script**: `05_train.py`

### Ground Truth Strategy

The 2,600 artists are "suspicious" not confirmed. Create cleaner labels using Phase 1-4 data:

| Label | Criteria | Expected Count | Role in Training |
|-------|---------|---------------|-----------------|
| **CONFIRMED_PFC** | Matches bad actor DB (label OR producer) AND missing from 4+ platforms | ~400-800 | Positive class |
| **LIKELY_PFC** | Missing 4+ platforms + high-PFC-rate producer/label, but not in bad actor DB | ~800-1200 | Validation/test only |
| **UNCERTAIN** | Mixed signals, some platforms, no bad actor connections | ~400-600 | Excluded |
| **LIKELY_LEGIT** | 5+ platforms, physical releases or live shows, no bad actor connections | ~100-200 | Negative class |
| **CONTROL** | External control group of verified legitimate artists | 200-300 | Negative class |

### Model Architecture

**Primary: Random Forest / XGBoost**
- Handles mixed feature types
- Provides feature importance rankings (directly informs scoring weights)
- Works with small datasets (~600-1000 training examples)

**Secondary: Logistic Regression with L1 regularization**
- Interpretable coefficients ("each missing platform increases PFC probability by X%")
- Natural feature selection

**Validation**: 5-fold stratified cross-validation. Report precision, recall, F1, AUC-ROC.

**Optimize for precision over recall** — better to miss some PFC artists than wrongly flag legitimate ones.

### Output

`model/validation_report.json`:
```json
{
  "model": "xgboost",
  "cv_folds": 5,
  "precision": 0.94,
  "recall": 0.78,
  "f1": 0.85,
  "auc_roc": 0.96,
  "feature_importances": [
    {"feature": "has_discogs_physical", "importance": 0.18},
    {"feature": "platform_count", "importance": 0.15},
    {"feature": "max_producer_corpus_pct", "importance": 0.12},
    {"feature": "label_exclusivity_score", "importance": 0.10}
  ],
  "confusion_matrix": {
    "true_positive": 312,
    "false_positive": 20,
    "true_negative": 238,
    "false_negative": 87
  }
}
```

---

## Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Name disambiguation errors** | Wrong artist data pollutes corpus | Track confidence levels. Analyze ambiguous cases separately. Require manual review for low-confidence. |
| **API rate limit changes** | Stalls enrichment mid-run | Adaptive backoff. Per-API resumability. Only re-run affected platform. |
| **Legitimate artists in PFC playlists** | False positives in training data | LIKELY_LEGIT category excluded from positive training set. Control group provides clean negatives. |
| **Overfitting to known patterns** | Model learns Johan Röhr's patterns but fails on new operations | Use structural features (platform_count, singles_ratio) as primary. Entity matching is separate rule-based layer. |
| **PFC operations evolve** | Fake presences become more convincing | Entity graph is resilient — network structure (shared producers, label co-occurrence) is much harder to fake than individual signals. |
| **Bulk API calls trigger blocks** | IP-level rate limiting | Spread over multiple days. Proper User-Agent strings. Stay within documented limits. |

---

## Claude Code Session Plan

Break the work into focused sessions:

| Session | Focus | Deliverable |
|---------|-------|------------|
| **1** | Build enrichment infrastructure | `api_clients.py`, `rate_limiter.py`, `01_enrich.py`. Test with 5 seed artists. Validate schema. |
| **2-6** | Run enrichment batches | ~500 artists/session. Resumable design means each session just runs the script. |
| **7** | Build entity extraction | `entity_extractor.py` + `02_expand.py`. Process all enriched profiles → entity graph. |
| **8** | Investigate high-connectivity entities | Deeper queries on producers/labels with 5+ artist connections. Discover new bad actors. |
| **9** | Pattern mining | `03_mine.py`: naming, temporal, cross-platform, label network analysis. |
| **10** | Build control group | Enrich 200-300 known-legitimate similar-genre artists through same pipeline. |
| **11** | Feature engineering + validation | `04_validate.py`: build feature matrix, run statistical tests, rank signals. |
| **12** | Model training | `05_train.py`: train classifier, generate validation report, output revised scoring weights. |

---

## What This Pipeline Discovers That We Cannot See Today

The core value of analyzing 2,600 artists in bulk (vs. one at a time):

1. **Producer networks**: If 200 artists share the same 5 songwriters → invisible when examining one artist, obvious in aggregate
2. **Shell label identification**: Labels that exist ONLY in the PFC corpus and nowhere in legitimate music
3. **Industrial release patterns**: Fixed cadences, batch releases, suspicious clustering around editorial refresh dates
4. **Naming fingerprints**: Generated names have statistical signatures (phonemic patterns, cultural mismatches, length distributions)
5. **Platform presence profiles**: The exact combination of platforms that distinguishes PFC from authentic (quantified, not guessed)
6. **Supply chain mapping**: Which distributors funnel PFC content, at what concentration
7. **New bad actors**: Entities not yet documented by journalists but revealed by graph analysis
8. **Evidence-based scoring weights**: Replace hand-tuned heuristics with statistically validated signal importance

The 2,600 artists are the seed. What grows from them is the real product.
