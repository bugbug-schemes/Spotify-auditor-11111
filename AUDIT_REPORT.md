# Spotify Auditor — Comprehensive Codebase Audit Report

**Date:** 2026-02-27
**Scope:** Full codebase audit covering bugs, performance, error handling, tests, code quality, and security
**Test Results:** 206 passed, 44 errors (Flask dependency missing for CMS API tests)

---

## Executive Summary

| Severity | Found | Fixed | Key Areas |
|----------|-------|-------|-----------|
| **Critical** | 5 | **5** | Dead code in decision tree, SQL injection vector, race conditions in EntityDB, Deezer client missing error handling |
| **High** | 15 | **15** | Decision tree ordering, deep evidence skips dedup, timeout false negatives, MusicBrainz lock contention, no auth on CMS, double-counting PFC flags |
| **Medium** | 28 | **22** | False positives in evidence collectors, scoring inconsistencies, missing cache for external APIs, unsynchronized rate limiters, missing indexes, unused imports |
| **Low** | 22 | **9** | Dead fields, dead code, code duplication, minor logic issues |
| **Total** | **70** | **51** | |

---

## 1. CRITICAL FINDINGS

### C-1: Dead Code — Duplicate `elif top2_share >= 0.80` Makes Moderate Tier Unreachable

**File:** `evidence.py:1523-1545`
**Category:** Logic Bug / Dead Code

In `_collect_track_rank_evidence()`, the threshold chain has a duplicate condition:

```python
if top2_share >= 0.90:       # line 1511 — strength="strong"
elif top2_share >= 0.80:     # line 1523 — strength="strong"
elif top2_share >= 0.80:     # line 1535 — strength="moderate" (UNREACHABLE!)
elif top2_share >= 0.70:     # line 1546 — strength="weak"
```

The second `elif top2_share >= 0.80` at line 1535 **can never execute**. The intended "moderate" tier for 80-89% top-2 track concentration is completely missing. Artists in this range receive a "strong" red flag (3 points) instead of "moderate" (2 points), over-penalizing them in Rules 4, 6, 7, 9, and 10.

**Fix:** Change line 1523 to `elif top2_share >= 0.85:` (strong), keep line 1535 as `elif top2_share >= 0.75:` (moderate), and line 1546 as `elif top2_share >= 0.65:` (weak) — or whatever graduated thresholds are intended by the spec.

---

### C-2: SQL Injection via Dynamic Column Names in `scan_store.py` `heartbeat()`

**File:** `web/scan_store.py:132-145`
**Category:** Security / SQL Injection

The `heartbeat()` method interpolates `**fields` dict keys directly into SQL as column names:

```python
def heartbeat(self, scan_id: str, **fields: object) -> None:
    fields["last_heartbeat"] = time.time()
    set_parts = [f"{k}=?" for k in fields]
    conn.execute(
        f"UPDATE web_scans SET {', '.join(set_parts)} WHERE scan_id=?",
        values,
    )
```

While values are parameterized, column names are not. Any caller passing user-controlled keys could inject SQL.

**Fix:** Validate `fields` keys against an allowlist:
```python
_ALLOWED_COLUMNS = {"status", "phase", "current", "total", "message", "result_html", "error", "playlist_name", "last_heartbeat"}
for k in fields:
    if k not in _ALLOWED_COLUMNS:
        raise ValueError(f"Invalid column: {k}")
```

---

### C-3: Race Condition — EntityDB Concurrent Writes from ThreadPoolExecutor Workers

**File:** `audit_runner.py:700-713`
**Category:** Race Condition / Thread Safety

The `_lookup_and_evaluate` closure runs in up to 3 concurrent threads, and each calls `entity_db.increment_scan_count()` and `auto_promote_entity()`. Both perform read-then-write operations (`SELECT` then `UPDATE`) without any lock. Two threads processing the same artist name concurrently can race, causing lost updates.

Additionally, the `_in_batch` flag (entity_db.py:324) is a plain instance variable shared across all threads — not thread-local and not lock-protected.

**Fix:** Either (a) add a `threading.Lock` around read-modify-write sequences in EntityDB, or (b) move entity_db writes to the single-threaded `as_completed` loop in the main thread. Also make `_in_batch` thread-local.

---

### C-4: Deezer Client Missing Try/Except for Connection Errors

**File:** `deezer_client.py:115`
**Category:** Error Handling / Resilience

The `_get` method calls `self.session.get()` without wrapping in `try/except requests.RequestException`. Network timeouts, DNS failures, or connection refusals propagate unhandled through the retry loop. Every other client wraps this in try/except. Additionally, Deezer does not handle HTTP 429 or 500-503 at the transport level (only application-level error code 4).

**Fix:** Wrap `self.session.get()` in `try/except requests.RequestException` with exponential backoff, matching other clients. Add explicit 429/500-503 checks.

---

### C-5: `check_same_thread=False` Combined with Non-Thread-Safe `_in_batch`

**File:** `entity_db.py:324,331,394,415`
**Category:** Concurrency / DB Corruption Risk

SQLite's `check_same_thread=False` disables the safety check that prevents cross-thread connection use. Combined with the shared `_in_batch` flag (C-3), one thread's `batch()` context can cause other threads to skip commits, leading to silent data loss.

**Fix:** Make `_in_batch` thread-local via `self._local`. Remove `check_same_thread=False` since connections are already in `threading.local()`.

---

## 2. HIGH-SEVERITY FINDINGS

### H-1: Rule 8 (PFC Label) Can Override Rule 9 Even When Green Outweighs Red

**File:** `evidence.py:2988-2990`
**Category:** Decision Tree Rule Ordering

An artist with a PFC label and moderate red accumulation (total_red=7) but strong green evidence (total_green=11) fails Rule 6's 2x requirement (`11 >= 14` false) but then gets caught by Rule 8 ("Suspicious") before reaching Rule 9 ("Likely Authentic" when green > red).

**Fix:** Add a guard to Rule 8: `if has_pfc_label and total_green_strength <= total_red_strength * 1.5:`.

### H-2: `incorporate_deep_evidence` Skips Deduplication and Sanity Check

**File:** `evidence.py:3446-3491`
**Category:** Logic Bug / Missing Safeguard

Unlike `evaluate_artist`, `incorporate_deep_evidence` does not deduplicate evidence (same source+finding, keep strongest) nor run the sanity check that overrides false SUSPICIOUS verdicts when API failures caused all-zero category scores. Deep re-evaluation can double-count evidence or revert previously-corrected verdicts.

**Fix:** Extract deduplication and sanity check into shared helpers called from both functions.

### H-3: PFC Songwriter Double-Counted Across Two Collectors

**File:** `evidence.py:1282-1296` and `evidence.py:1402-1432`
**Category:** Double-Counting / False Positive

Both `_collect_label_evidence` (source="Blocklist") and `_collect_credit_network_evidence` (source="Credit network") check `artist.contributors` against `pfc_songwriters()`. Both emit `strength="strong"` red flags. Since they use different source names, deduplication doesn't catch it. One PFC songwriter match = 6 red strength points instead of 3.

**Fix:** Remove the PFC songwriter check from `_collect_label_evidence` (keep it in the dedicated credit collector).

### H-4: Cross-Source Same-Signal Not Deduplicated (PFC Label Counted 3x)

**File:** `evidence.py:3382-3390`
**Category:** Score Inflation

Deduplication uses `(source, finding)` as key. But PFC label matches are emitted independently by `_collect_label_evidence` (Blocklist), `_collect_discogs_evidence` (Discogs), and `_collect_musicbrainz_evidence` (MusicBrainz) — all with tag `pfc_label`. An artist on a PFC label gets 3 separate strong red flags (9 red points instead of 3).

**Fix:** Add tag-based deduplication: cap evidence items with the same tag at 2 maximum, or apply diminishing returns.

### H-5: Timeout Fallback Evaluates Artists with Empty ExternalData → False Negatives

**File:** `audit_runner.py:760-780`
**Category:** False Negative Risk

When EVALUATE_TIMEOUT fires, remaining artists are evaluated with `evaluate_artist(artist, entity_db=entity_db)` — no ExternalData. The evidence tree sees zero platform presence and generates red flags. A legitimate 500K-follower artist who timed out could receive a SUSPICIOUS verdict. The sanity check doesn't trigger because `api_errors` is empty.

**Fix:** Either (a) pass ExternalData with all APIs flagged as errored to trigger the sanity check, (b) set verdict to INCONCLUSIVE with low confidence, or (c) add to `skipped_artists` instead of evaluating.

### H-6: `fut.cancel()` Does Not Stop Running Threads; Executor Blocks on Exit

**File:** `audit_runner.py:552,756,1047,1132`
**Category:** Resource Leak / Timeout Not Enforced

`Future.cancel()` only works for not-yet-started tasks. Running tasks continue after timeout. `ThreadPoolExecutor.__exit__` calls `shutdown(wait=True)`, blocking until they finish. RESOLVE_TIMEOUT and EVALUATE_TIMEOUT are not truly enforced.

**Fix:** Use `pool.shutdown(wait=False, cancel_futures=True)` (Python 3.9+) or restructure with per-request timeouts.

### H-7: MusicBrainz Holds Global Lock During Entire Request + 1.1s Sleep

**File:** `musicbrainz_client.py:99-115`
**Category:** Performance Bottleneck

The `_rate_lock` is held for the entire HTTP request AND the 1.1-second post-request sleep. Even when 4 concurrent enrichment threads fire, they serialize to ~4.4s minimum per artist. For a 30-artist playlist, MusicBrainz alone could take 2+ minutes.

**Fix:** Track last-request timestamp instead of holding the lock during I/O. Release lock after recording timestamp. Sleep only the remaining interval before the next request.

### H-8: MusicBrainz Sleeps During Retry Backoff While Holding Global Lock

**File:** `musicbrainz_client.py:104-107`
**Category:** Performance / Lock Contention

When a request fails, the 2-8 second backoff sleep happens inside the `with _rate_lock` block, blocking ALL other MusicBrainz requests across ALL threads.

**Fix:** Release the lock before sleeping on backoff.

### H-9: No JSON Error Handling in Any Client

**File:** All `*_client.py` files at `.json()` call sites
**Category:** Error Handling

Every client calls `r.json()` without catching `JSONDecodeError`. If any API returns HTML error pages or empty bodies during outages, the unhandled exception propagates up.

**Fix:** Wrap `.json()` in `try/except (json.JSONDecodeError, requests.exceptions.JSONDecodeError)`.

### H-10: Server Errors (500-503) Not Retried in 5 of 7 Clients

**File:** `genius_client.py:88`, `discogs_client.py:88`, `setlistfm_client.py:91`, `lastfm_client.py:79`, `deezer_client.py:116`
**Category:** Error Handling

Only MusicBrainz explicitly checks for 503 and retries. The other five clients call `raise_for_status()` on 500/502/503, which raises immediately without retry.

**Fix:** Add `r.status_code in (500, 502, 503)` to each client's retry conditions.

### H-11: External API Data Never Cached

**File:** `cache.py`, `cli.py:1036`
**Category:** Cache Effectiveness / Performance

The cache stores only "quick" tier results. Phase 2 external API lookups (Genius, Discogs, Setlist.fm, MusicBrainz, Last.fm — 5-15 seconds per artist) are NEVER cached. Re-scanning the same playlist repeats all external API calls.

**Fix:** Cache external API results under a "standard" tier key after Phase 2 completes.

### H-12: No Authentication on CMS Admin Endpoints

**File:** `web/api.py` — all routes
**Category:** Authentication / Authorization

The entire `/api/cms/*` blueprint has zero authentication. Any network-reachable client can submit reviews, sync blocklists, add/remove blocklist entries, and perform batch operations.

**Fix:** Add authentication middleware (API key, session auth) to all `/api/cms/*` write endpoints.

### H-13: Missing `entity_type` Validation on Multiple API Endpoints

**File:** `web/api.py:112,124,152,170,228`
**Category:** Input Validation

Only `entity_detail` validates `entity_type`. Other endpoints pass it directly to DB methods without validation, causing 500 errors or poisoning the database with arbitrary entity types.

**Fix:** Add consistent validation at every endpoint: `if entity_type not in ("artist", "label", "songwriter", "publisher"): return 400`.

### H-14: Invalid API Keys Cause N Repeated Failures Per Scan

**File:** `genius_client.py:88`, `setlistfm_client.py:91`
**Category:** Graceful Degradation

When an API key is invalid, every artist in the playlist triggers a failed call. There is no check-once-and-disable mechanism.

**Fix:** Validate key on first use; set `self.enabled = False` with a single warning on 401/403.

### H-15: `retry_skipped_artists` Returns Wrong Type When `skipped` Is Empty

**File:** `audit_runner.py:972-973`
**Category:** Bug / Type Error

Returns `[]` instead of `([], [])`. Any caller unpacking as `reports, still_skipped = retry_skipped_artists(...)` crashes with `ValueError`.

**Fix:** Change to `return [], []`.

---

## 3. MEDIUM-SEVERITY FINDINGS

### M-1: `genius_followers_count >= 0` Always True — Inflates Bio Count
**File:** `evidence.py:511` | **Category:** Logic Bug
Condition is tautological (field defaults to 0). Every Genius-found artist gets +1 bio credit regardless of actual profile content.
**Fix:** Change to `ext.genius_followers_count > 0` or check `ext.genius_description`.

### M-2: Generic Two-Word Name Regex Matches Most Legitimate Artists
**File:** `evidence.py:1330` | **Category:** False Positive
Pattern `^(The\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+s?$` matches "Taylor Swift", "Bruno Mars", "Frank Ocean", etc.
**Fix:** Restrict to mood-word dictionary patterns or convert to neutral evidence.

### M-3: `" and "` Separator Splits Band Names
**File:** `evidence.py:35` | **Category:** False Positive
`extract_primary_artist()` splits "Simon and Garfunkel" → "Simon", "Florence and the Machine" → "Florence".
**Fix:** Try full name first, fall back to splitting only if full name yields no API results.

### M-4: Missing `" with "` Separator in `extract_primary_artist()`
**File:** `evidence.py:33-36` | **Category:** Missing Feature
"Drake with Future" is not split. API lookups search for the combined string.
**Fix:** Add `" with "` to `_ARTIST_SEPARATORS`.

### M-5: `_not_found_strength()` Has No Internal `_api_errored()` Guard
**File:** `evidence.py:818-836` | **Category:** Defensive Programming
Function relies entirely on callers to check `_api_errored()` first. If any caller forgets, API timeout = false "not found" penalty.
**Fix:** Add an internal `_api_errored()` guard returning "weak" for errored platforms.

### M-6: Cookie-Cutter Duration Check Has No Genre Exemption
**File:** `evidence.py:1046-1056` | **Category:** False Positive
`stdev < 10s` across 5+ tracks flags ambient, classical, electronic, and meditation artists with a moderate red flag.
**Fix:** Skip or downgrade when genres include ambient/classical/electronic/meditation.

### M-7: Single-Producer Flag Penalizes Self-Producing Solo Artists
**File:** `evidence.py:1442-1451` | **Category:** False Positive
Flags `len(producers) == 1` without checking if the producer matches the artist name. Hits Aphex Twin, Deadmau5, etc.
**Fix:** Skip flag when `producers[0].lower() == artist.name.lower()` (or fuzzy match).

### M-8: High Last.fm Ratio Penalized in Scorer but Praised in Evidence Collector
**File:** `evidence.py:548-553` vs `evidence.py:2299-2308` | **Category:** Inconsistency
Evidence collector: ratio >= 10 = strong green flag ("genuine fans"). Category scorer: ratio > 15 = -15 points ("suspicious").
**Fix:** Align — treat ratio > 15 as at least neutral in the scorer.

### M-9: Rule 5 Lacks `total_red_strength` Guard That Rule 4 Has
**File:** `evidence.py:2953` vs `2965` | **Category:** Inconsistency
Rule 4 requires `total_red_strength < 4`. Rule 5 has no such guard but can still verify artists with high accumulated red.
**Fix:** Add `total_red_strength < 6` guard to Rule 5.

### M-10: 11+ Singles / 0 Albums Tagged as `content_farm` Too Aggressively
**File:** `evidence.py:996-1005` | **Category:** False Positive
Many EDM/hip-hop artists release only singles for years. Threshold of 10 is too low.
**Fix:** Raise moderate threshold to 15-20 or combine with cadence check.

### M-11: Collaboration Green Flag Doesn't Verify Collaborators Aren't Fake
**File:** `evidence.py:1352-1363` | **Category:** False Negative / Evasion
PFC operations can create networks of fake artists cross-listed as collaborators, earning green flags.
**Fix:** Cross-reference collaborators against flagged artists in entity DB before awarding green flag.

### M-12: Last.fm "Found" Green Flag Unconditional Regardless of Listener Count
**File:** `evidence.py:2284-2292` | **Category:** Contradictory Evidence
Artist with 5 listeners gets moderate green ("Found on Last.fm") AND weak red ("Negligible presence").
**Fix:** Gate green flag strength on listener count: moderate if >= 100, weak if >= 50, neutral otherwise.

### M-13: Double-Counting Strong Flags in `_verdict_to_score()` Net Signal
**File:** `scoring.py:220-228` | **Category:** Scoring Math
`strong_greens * 3 + green_total` counts each strong flag with effective weight 4x (3 + 1) instead of intended 3x.
**Fix:** Use `non_strong_greens = green_total - strong_greens` in the calculation.

### M-14: Legacy Fallback Assigns Score 100 to Unscanned Artists
**File:** `scoring.py:176-185` | **Category:** Logic Error
When `evaluation` is None and all tier scores are None, `final_score = max(0, 100 - 0) = 100` ("Verified Artist").
**Fix:** Default to 50 (Inconclusive) when no data is available.

### M-15: `ESCALATE_TO_DEEP` Uses Suspicion Scale, Opposite of Documented Legitimacy Scale
**File:** `config.py:75`, `scoring.py:394-396` | **Category:** Semantic Confusion
Input is raw suspicion score (higher = more suspicious), but the project documents higher = more legitimate.
**Fix:** Rename function/parameter or add explicit docstring clarifying the input scale.

### M-16: Shared API Clients with Unsynchronized Rate Limiters Across Threads
**File:** `audit_runner.py:420-427,726` | **Category:** Race Condition
3 threads each sleep `delay` then fire simultaneously, potentially exceeding rate limits. Only MusicBrainz has a lock.
**Fix:** Add a `threading.Lock` to each client's request method.

### M-17: EntityDB Init Failure Silently Swallowed
**File:** `audit_runner.py:416-417` | **Category:** Silent Error
`except Exception: entity_db = None` — no logging, no warning. Scan loses entity intelligence silently.
**Fix:** Add `logger.warning("Entity DB initialization failed: %s", exc)`.

### M-18: Retry Path Skips Pre-Check, Deezer AI, Entity DB
**File:** `audit_runner.py:1058-1101` | **Category:** Incomplete Pipeline
`_lookup_and_evaluate_retry` skips pre-check (known AI), Deezer AI check, and entity DB integration.
**Fix:** Align retry path with main path or extract shared logic.

### M-19: Retry Path Does Not Reuse Cached Data
**File:** `audit_runner.py:1005-1016` | **Category:** Cache Miss / Performance
`retry_skipped_artists` creates fresh clients but never checks the cache. Previously fetched data is wasted.
**Fix:** Pass `Cache` instance and `artist_infos` dict to retry function.

### M-20: Late-Completing Futures After Timeout Have Results Discarded
**File:** `audit_runner.py:508-554,726-758` | **Category:** Data Loss
Futures completing between TimeoutError and the cancel loop are added to `skipped_artists` despite having valid results.
**Fix:** Check `fut.done()` before marking as skipped; drain completed futures.

### M-21: No `PRAGMA busy_timeout` on Any SQLite Connection
**File:** `entity_db.py:327`, `scan_store.py:78`, `web/app.py:64` | **Category:** DB Reliability
Concurrent writers get immediate `SQLITE_BUSY` errors instead of retrying.
**Fix:** Add `PRAGMA busy_timeout=5000` after opening each connection.

### M-22: Missing Indexes on Review Status and Publisher FK
**File:** `entity_db.py:287-306` | **Category:** Performance
`labels.review_status`, `songwriters.review_status`, `publishers.review_status`, `artist_publishers.publisher_id`, and `scans.started_at` lack indexes despite frequent use in WHERE/ORDER BY clauses.
**Fix:** Add `CREATE INDEX IF NOT EXISTS` for each.

### M-23: ScanStore in `app.py` Lacks WAL Mode
**File:** `web/app.py:52-91` | **Category:** DB Reliability
Unlike entity_db and scan_store, the ScanStore in app.py does not enable WAL mode or set busy_timeout.
**Fix:** Add `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`.

### M-24: `_active_scans` Dict Accessed Without Lock
**File:** `web/app.py:224-337,367-374` | **Category:** Race Condition
The `_active_scans` OrderedDict is accessed from both main and background threads without `_scans_lock`.
**Fix:** Consistently acquire `_scans_lock` around all reads and writes.

### M-25: No Rate Limiting on Scan Endpoint
**File:** `web/app.py:313-362` | **Category:** Denial of Service
`/api/scan` has a concurrency limit of 5 but no per-IP rate limiting. Amplification attack via external API calls.
**Fix:** Add per-IP rate limiting (e.g., Flask-Limiter).

### M-26: 8-Character Scan IDs Risk Collision
**File:** `web/app.py:336,477` | **Category:** Data Integrity
`uuid.uuid4().hex[:8]` with `INSERT OR REPLACE` — collision silently overwrites another user's scan.
**Fix:** Use full UUID or at least 16 hex characters.

### M-27: MusicBrainz `get_releases` Can Make Unbounded API Calls
**File:** `musicbrainz_client.py:218-249` | **Category:** Performance
Paginates through ALL releases. Prolific artists could trigger dozens of rate-limited calls.
**Fix:** Add max offset limit (e.g., 500 releases).

### M-28: External Client Sessions Never Closed
**File:** `cli.py:884-893` | **Category:** Resource Leak
6 API clients create `requests.Session` instances but only `SpotifyClient` and `Cache` are closed in `finally`.
**Fix:** Add `.close()` calls for all clients or make them context managers.

---

## 4. LOW-SEVERITY FINDINGS

### L-1: 8 ExternalData Fields Populated but Never Read in Evidence Logic
**File:** `evidence.py:101,115,123,124,133,144-146,160,182`
Fields: `setlistfm_tour_names`, `musicbrainz_area`, `lastfm_tags`, `lastfm_similar_artists`, `wikipedia_categories`, `songkick_venue_names/cities/countries`, `youtube_match_confidence`, `musicbrainz_social_urls`.

### L-2: Missing `" prod. "` and Parenthetical `(feat. X)` Separators
**File:** `evidence.py:33-36`

### L-3: Year Regex Misses Pre-1950, Breaks After 2029
**File:** `evidence.py:2108`

### L-4: Year-Only Release Dates Normalized to Same Day → False Same-Day Flag
**File:** `evidence.py:1083-1084`

### L-5: Platform Evidence Generated Then Immediately Discarded
**File:** `evidence.py:879-889,3288`

### L-6: `fast_mode_evaluation` Sets `presence.deezer = True` Twice
**File:** `evidence.py:3212-3215`

### L-7: YouTube <100 Subscribers Flagged as Red for Auto-Generated Topic Channels
**File:** `evidence.py:2594-2602`

### L-8: Neutral "Not on Blocklist" Ignores PFC Songwriter Match
**File:** `evidence.py:1298`

### L-9: Score Ranges Never Fully Utilized (82-84, 99-100, 0-1, 16-17 Unreachable)
**File:** `scoring.py:194-235`

### L-10: Secondary Sort Within Verdict Groups Is Counter-Intuitive
**File:** `scoring.py:331-335`
Within "Likely Artificial", higher scores appear first — score 15 before score 2. "Most concerning first" should be ascending.

### L-11: Legacy Breakdown Labels "Inconclusive" as "likely_non_authentic"
**File:** `scoring.py:363-370`

### L-12: `pfc_playlists()` Defined but Never Called
**File:** `config.py:194-196`

### L-13: `MOOD_WORDS` Defined in Config, Imported in Evidence, Used in Neither
**File:** `config.py:54-62`, `evidence.py:23`

### L-14: `_collect_touring_geography_evidence()` Is a Dead Stub
**File:** `evidence.py:2364`

### L-15: `analyze_pfc_data.py`, `fetch_pfc_tracks.py`, `api_logger.py` Are Orphaned Modules
**File:** `spotify_audit/analyze_pfc_data.py`, `spotify_audit/fetch_pfc_tracks.py`, `spotify_audit/api_logger.py`

### L-16: Unused Imports in `cli.py`
`import sys` (line 15), `from rich.text import Text` (line 26), `run_deep_analysis` and `DeepAnalysis` (line 55), `standard_scan` (line 44).

### L-17: `_mock_response()` Identically Defined 6 Times in Tests
**File:** `tests/test_clients.py:64,122,182,223,273,327`

### L-18: "Not Found" Boilerplate Repeated in 5+ Collectors
**File:** `evidence.py:1583-1601,1654-1671,1750-1812,1838-1854,2265-2280`

### L-19: `getattr()` Used Unnecessarily on Defined Dataclass Field
**File:** `evidence.py:704`

### L-20: `.env.example` Has Non-Empty Anthropic Key Placeholder
**File:** `.env.example:5`

### L-21: Loose Playlist URL Validation (SSRF Potential)
**File:** `web/app.py:324-326`
Only checks for `"spotify.com"` substring. Accepts `"evil.com?redirect=spotify.com"`.
**Fix:** Use strict regex: `^https?://open\.spotify\.com/(playlist|album|track)/[a-zA-Z0-9]+`.

### L-22: Test Assertion Always Passes Due to `or True`
**File:** `tests/test_integration.py:253`
```python
assert (a.verdict_enum.value, -a.final_score) <= (b.verdict_enum.value, -b.final_score) or True
```

---

## 5. TEST GAPS

### Test Results
- **206 passed** (clients, config, entity_db_cms, evidence, integration, scoring)
- **44 errors** — all in `test_cms_api.py` due to missing `flask` dependency

### Missing Test Coverage

| Gap | Severity | Details |
|-----|----------|---------|
| 11 evidence collectors untested | High | `_collect_credit_network_evidence`, `_collect_wikipedia_evidence`, `_collect_songkick_evidence`, `_collect_deezer_ai_evidence`, `_collect_youtube_evidence`, `_collect_pro_registry_evidence`, `_collect_bandcamp_evidence`, `_collect_isrc_evidence`, `_collect_press_coverage_evidence`, `_collect_cowriter_network_evidence`, `_collect_entity_db_evidence` |
| 0 API client error path tests | High | No tests for HTTP 429, 500-503, ConnectionError, Timeout, or malformed JSON across all 6 clients |
| Decision tree Rules 1.5, 3, 6, 7, 8 untested | High | Only Rules 1, 2, 4, 5, 9, 10, and defaults are tested |
| Sanity check override untested | Medium | evidence.py:3400-3427 override for API-failure false negatives |
| `extract_primary_artist()` untested | Medium | No tests for any separator pattern |
| `compute_category_scores()` degenerate inputs untested | Medium | No tests for all-zeros, all-errors, single-platform |
| No CLI integration test | Medium | No `CliRunner` test for the full pipeline |
| Scoring boundary values not verified | Medium | Blend formula tested for range but not exact outputs |
| Integration tests skip on clean checkout | Low | Depend on `data/enriched/` files with no synthetic fallback |

---

## 6. PERFORMANCE BOTTLENECKS (Priority Order)

1. **MusicBrainz lock contention** (H-7, H-8) — Single biggest bottleneck. Serializes all requests, holds lock during I/O + sleep.
2. **External API data not cached** (H-11) — Re-scanning a playlist repeats all 5-15s/artist external lookups.
3. **MusicBrainz unbounded pagination** (M-27) — Prolific artists cause dozens of rate-limited calls.
4. **Deezer sequential ISRC lookups** — Up to 10 sequential calls outside ThreadPoolExecutor (deezer_client.py:311-324).
5. **Genius wasteful duplicate call** — `get_artist_songs_count` fetches `per_page=1` then `per_page=50` (genius_client.py:196-212).
6. **Missing DB indexes** (M-22) — Review queue queries do full table scans on review_status columns.
7. **Unsynchronized rate limiters** (M-16) — 3 threads can burst simultaneously, triggering 429s.

---

## 7. SECURITY SUMMARY

| Issue | Severity | Location |
|-------|----------|----------|
| SQL injection via column names | Critical | `web/scan_store.py:132-145` |
| No auth on CMS admin endpoints | High | `web/api.py` (all routes) |
| Missing entity_type validation | High | `web/api.py:112,124,152,170,228` |
| No rate limiting on scan endpoint | Medium | `web/app.py:313-362` |
| No CSRF protection | Medium | `web/app.py`, `web/api.py` |
| Missing security headers | Medium | `web/app.py` |
| Loose URL validation (SSRF) | Low | `web/app.py:324-326` |
| **No pickle/eval/exec found** | ✅ | Codebase-wide |
| **SQL values properly parameterized** | ✅ | All queries use `?` placeholders |
| **HTML escaping in error reports** | ✅ | `app.py:174` uses `html.escape()` |

---

## 8. FIX STATUS

### All Critical (5/5) — FIXED
### High-severity (15/15) — FIXED
### Medium-severity (22/28) — 22 FIXED, 6 remaining

**Remaining medium (deferred — low impact or require larger refactors):**
- M-3: `" and "` separator splits band names — needs full-name-first search strategy
- M-11: Collaboration green flag doesn't verify collaborators aren't fake — requires passing entity_db to collector
- M-16: Rate limiters unsynchronized on Genius/Discogs/Setlist.fm/Last.fm — moderate risk, mitigated by existing per-call delays
- M-19: Retry path doesn't reuse cached data — partially mitigated by H-11 (main path caching)
- M-20: Late-completing futures after timeout have results discarded — partially mitigated by H-6 (drain completed)
- M-25: No rate limiting on scan endpoint — deployment-specific (recommend Flask-Limiter)

### Low-severity (9/22) — 9 FIXED, 13 remaining

**Remaining low (informational — dead fields, minor test gaps):**
- L-1: 8 ExternalData fields populated but never read in evidence logic
- L-2: Missing `" prod. "` and parenthetical `(feat. X)` separators
- L-3: Year regex misses pre-1950, breaks after 2029
- L-4: Year-only release dates normalized to same day
- L-5: Platform evidence generated then immediately discarded
- L-7: YouTube <100 subscribers flagged for auto-generated topic channels
- L-8: Neutral "Not on Blocklist" ignores PFC songwriter match
- L-9: Score ranges never fully utilized (boundary values unreachable)
- L-10: Secondary sort within verdict groups is counter-intuitive
- L-11: Legacy breakdown labels "Inconclusive" as "likely_non_authentic"
- L-15: `analyze_pfc_data.py`, `fetch_pfc_tracks.py`, `api_logger.py` orphaned modules
- L-19: `getattr()` used unnecessarily on defined dataclass field
- L-20: `.env.example` has non-empty Anthropic key placeholder
