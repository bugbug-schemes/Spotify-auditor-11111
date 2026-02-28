# Code Quality Fix Plan — Claude Code Implementation Guide

> **Purpose:** Self-contained implementation spec. Claude Code should be able to execute every task below without needing to ask questions or reference other documents.

---

## How to Use This Document

Work through Sprints 1 → 2 → 3 in order. Within each sprint, tasks are numbered by priority. Each task includes: the problem, where the code lives, exactly what to change, test cases, and what NOT to break.

Before starting any task, `grep` for the function/variable names listed to confirm exact file locations — the codebase may have shifted since this was written.

---

## Sprint 1: Detection Accuracy

These fixes directly affect whether artists receive correct verdicts. Do these first.

---

### Task 1.1 — Fix `extract_primary_artist()` Band Name Splitting

**Issue ID:** M-3 + L-2 (combined — same function, same problem class)

**Problem:** `extract_primary_artist()` eagerly splits on `" and "`, turning real band names into wrong lookups:
- "Simon and Garfunkel" → "Simon" (wrong — searches for solo artist "Simon")
- "Florence and the Machine" → "Florence" (wrong)
- "Belle and Sebastian" → "Belle" (wrong)

Additionally, credits with `(feat. X)` or `prod.` are not handled:
- "Drake (feat. Future)" → searched as full string, fails lookup
- "Track prod. Metro Boomin" → searched as full string, fails lookup

**Locate the code:**
```bash
grep -rn "extract_primary_artist\|_ARTIST_SEPARATORS" --include="*.py"
```
This will likely be in the evaluation module or a utility file. The separator list is currently:
```python
SEPARATORS = [", ", " & ", " and ", " feat. ", " ft. ", " feat ", " ft ", " x "]
```

**What to change:**

Replace the entire `extract_primary_artist()` function with this two-phase approach:

```python
import re

# Phase 1: Strip parenthetical/bracket features BEFORE separator splitting
_FEATURED_PATTERN = re.compile(
    r'\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+.+?[\)\]]',
    re.IGNORECASE
)
_PROD_PATTERN = re.compile(
    r'\s+prod\.?\s+.+$',
    re.IGNORECASE
)

# Phase 2: Separators to split on — ordered by specificity
_ARTIST_SEPARATORS = [", ", " & ", " feat. ", " ft. ", " feat ", " ft ", " x ", " prod. ", " prod "]

# Known bands with " and " in the name — fast-path allowlist
# Add to this as edge cases are discovered
_AND_BANDS = {
    "simon and garfunkel",
    "florence and the machine",
    "belle and sebastian",
    "mumford and sons",
    "earth wind and fire",
    "earth, wind & fire",
    "tegan and sara",
    "hall and oates",
    "peter paul and mary",
    "crosby stills and nash",
    "crosby stills nash and young",
    "emerson lake and palmer",
    "blood sweat and tears",
    "huey lewis and the news",
    "tom petty and the heartbreakers",
    "echo and the bunnymen",
    "bob marley and the wailers",
    "sly and the family stone",
    "kool and the gang",
    "prince and the revolution",
    "a winged victory for the sullen",
}


def extract_primary_artist(credit: str) -> str:
    """Extract the primary artist name from a credit string.
    
    Strategy:
    1. Strip parenthetical features: "Drake (feat. Future)" → "Drake"
    2. Strip prod. credits: "Track prod. Metro Boomin" → "Track"
    3. Check if full (cleaned) name is a known "and" band → return as-is
    4. Split on separators, return first segment
    
    The caller should ALSO try the full unsplit name against APIs
    before using the split version. See lookup_artist_with_fallback().
    """
    if not credit or not credit.strip():
        return credit
    
    # Step 1: Strip parenthetical features
    cleaned = _FEATURED_PATTERN.sub('', credit).strip()
    
    # Step 2: Strip trailing prod. credits
    cleaned = _PROD_PATTERN.sub('', cleaned).strip()
    
    # Step 3: Check "and" band allowlist (case-insensitive)
    if cleaned.lower() in _AND_BANDS:
        return cleaned
    
    # Also check if the name contains " and the " — very likely a band name
    # e.g., "X and the Y" pattern
    if " and the " in cleaned.lower():
        return cleaned
    
    # Step 4: Split on separators
    for sep in _ARTIST_SEPARATORS:
        if sep in cleaned:
            return cleaned.split(sep)[0].strip()
    
    return cleaned
```

**Also add a fallback wrapper** that callers should use for API lookups:

```python
async def lookup_artist_with_fallback(credit: str, api_lookup_fn) -> dict | None:
    """Try full credit name first, fall back to extracted primary artist.
    
    This prevents splitting real band names like 'Florence and the Machine'.
    """
    # Try the full credit (after stripping feat./prod.) first
    cleaned = _FEATURED_PATTERN.sub('', credit).strip()
    cleaned = _PROD_PATTERN.sub('', cleaned).strip()
    
    result = await api_lookup_fn(cleaned)
    if result:
        return result
    
    # Full name failed — try the extracted primary artist
    primary = extract_primary_artist(credit)
    if primary != cleaned:
        result = await api_lookup_fn(primary)
        if result:
            return result
    
    return None
```

**Where to wire this in:** Find every place that calls `extract_primary_artist()` and feeds the result into an API lookup. Replace with `lookup_artist_with_fallback()` or equivalent logic. Key locations to grep for:
```bash
grep -rn "extract_primary_artist" --include="*.py"
```

**Test cases to verify:**

| Input | Expected Primary | Notes |
|---|---|---|
| `"Simon and Garfunkel"` | `"Simon and Garfunkel"` | Known band — allowlist |
| `"Florence and the Machine"` | `"Florence and the Machine"` | " and the " pattern |
| `"Roger Eno, Brian Eno"` | `"Roger Eno"` | Comma separator |
| `"Drake (feat. Future)"` | `"Drake"` | Parenthetical strip |
| `"Track prod. Metro Boomin"` | `"Track"` | Prod credit strip |
| `"A Winged Victory for the Sullen, Adam Wiltzie"` | `"A Winged Victory for the Sullen"` | Comma after long name |
| `"Max Richter, Grace Davidson"` | `"Max Richter"` | Comma separator |
| `"Bon Iver"` | `"Bon Iver"` | No separator — passthrough |
| `"KAYTRANADA (ft. Syd)"` | `"KAYTRANADA"` | Bracket variation |
| `"Gorillaz feat. De La Soul"` | `"Gorillaz"` | Non-parenthetical feat |
| `"Anderson .Paak & The Free Nationals"` | `"Anderson .Paak"` | & separator |

**Do NOT break:**
- The existing separator logic for `, ` and ` & ` — those still need to work
- Any caching that keys on artist name — the primary name is the cache key
- The decision tree and evidence collectors — they receive the looked-up data, not the raw credit string

---

### Task 1.2 — Validate Collaborators Against Blocklists (M-11)

**Problem:** Collector #8 (Collaboration Evidence) awards green flags for having collaborators without checking whether those collaborators are themselves PFC/fake artists. This is a known evasion vector — PFC operations create fake artist networks that cross-list each other.

**Current behavior** (from the scoring logic):
```
Collaboration Evidence (Collector #8):
- ≥3 collaborators → moderate green
- 1-2 collaborators → weak green
- ≥5 related artists on Deezer → moderate green
- 1-4 related artists → weak green
```

No validation that collaborators are legitimate. PFC operations can game this.

**Context — how blocklists work in this codebase:**
Three JSON blocklist files exist in `spotify_audit/blocklists/`:
- `known_ai_artists.json` — 2,600+ confirmed fake artist names
- `pfc_distributors.json` — Epidemic Sound, Firefly, Queenstreet, etc.
- `pfc_songwriters.json` — shared producer networks

These are already loaded for Label Evidence (Collector #6) and Name Evidence (Collector #7). The entity DB (`pfc_analyzer.db` SQLite) stores prior scan results with `blocklist_status` per artist.

**Locate the code:**
```bash
grep -rn "_collect_collaboration_evidence\|collaboration.*evidence\|Collaboration Evidence" --include="*.py"
```

Also find the blocklist loading:
```bash
grep -rn "known_ai_artists\|pfc_songwriters\|pfc_distributors\|load_blocklist\|blocklist" --include="*.py" | head -30
```

And find how `entity_db` is passed around:
```bash
grep -rn "entity_db" --include="*.py" | head -20
```

**What to change:**

1. **Update the function signature** to accept blocklist data and entity_db:

```python
def _collect_collaboration_evidence(
    artist_info,          # existing param
    blocklists: dict,     # ADD: {"ai_artists": set, "pfc_songwriters": set}
    entity_db=None,       # ADD: SQLite connection or None
) -> list:  # list of Evidence objects
```

2. **After collecting collaborator names, cross-reference them:**

```python
def _collect_collaboration_evidence(artist_info, blocklists, entity_db=None):
    evidence = []
    
    # --- Existing logic to extract collaborator names ---
    # From MusicBrainz artist-rels (collaborators, groups)
    # From Deezer related artists
    # Keep this part as-is, just capture the names into variables:
    collaborator_names = []  # populated by existing logic
    related_artists = []     # populated by existing logic (Deezer)
    
    # ... (existing extraction logic here) ...
    
    # --- NEW: Cross-reference against blocklists ---
    all_associated = set()
    all_associated.update(c.lower().strip() for c in collaborator_names if c)
    all_associated.update(r.lower().strip() for r in related_artists if r)
    
    ai_artists_set = {name.lower() for name in blocklists.get("ai_artists", [])}
    pfc_songwriters_set = {name.lower() for name in blocklists.get("pfc_songwriters", [])}
    
    flagged_collaborators = []
    for name in all_associated:
        if name in ai_artists_set:
            flagged_collaborators.append(name)
            continue
        if name in pfc_songwriters_set:
            flagged_collaborators.append(name)
            continue
        if entity_db:
            try:
                row = entity_db.execute(
                    "SELECT blocklist_status FROM artists WHERE LOWER(name) = ? AND blocklist_status IN ('confirmed_bad', 'suspected')",
                    (name,)
                ).fetchone()
                if row:
                    flagged_collaborators.append(name)
            except Exception:
                pass  # DB lookup failure should not block evaluation
    
    flagged_ratio = len(flagged_collaborators) / len(all_associated) if all_associated else 0
    
    # --- MODIFIED: Award green flags only if collaborators are clean ---
    if flagged_collaborators:
        # Emit a red flag instead of green
        display_names = ', '.join(flagged_collaborators[:3])
        if len(flagged_collaborators) > 3:
            display_names += f' (+{len(flagged_collaborators) - 3} more)'
        evidence.append(Evidence(
            finding=f"Collaborates with {len(flagged_collaborators)} flagged artist(s): {display_names}",
            source="collaboration_check",
            evidence_type="red_flag",
            strength="moderate" if flagged_ratio >= 0.5 else "weak",
            tags=["pfc_network"],
        ))
    else:
        # Original green flag logic — only when ALL collaborators are clean
        collab_count = len(collaborator_names)
        if collab_count >= 3:
            evidence.append(Evidence(
                finding=f"Has {collab_count} verified collaborators",
                source="musicbrainz",
                evidence_type="green_flag",
                strength="moderate",
                tags=[],
            ))
        elif collab_count >= 1:
            evidence.append(Evidence(
                finding=f"Has {collab_count} collaborator(s)",
                source="musicbrainz",
                evidence_type="green_flag",
                strength="weak",
                tags=[],
            ))
    
    # Related artists logic — only award green for clean related artists
    clean_related = [r for r in related_artists if r.lower().strip() not in {f.lower() for f in flagged_collaborators}]
    if len(clean_related) >= 5:
        evidence.append(Evidence(
            finding=f"{len(clean_related)} clean related artists on Deezer",
            source="deezer",
            evidence_type="green_flag",
            strength="moderate",
            tags=[],
        ))
    elif len(clean_related) >= 1:
        evidence.append(Evidence(
            finding=f"{len(clean_related)} related artist(s) on Deezer",
            source="deezer",
            evidence_type="green_flag",
            strength="weak",
            tags=[],
        ))
    
    return evidence
```

3. **Update the caller** in `evaluate_artist()` to pass blocklists and entity_db:

```bash
grep -rn "_collect_collaboration_evidence(" --include="*.py"
```

Change the call site from:
```python
collaboration_evidence = _collect_collaboration_evidence(artist_info)
```
to:
```python
collaboration_evidence = _collect_collaboration_evidence(artist_info, blocklists, entity_db)
```

Where `blocklists` is the same dict already loaded for label/name evidence. Grep for where `known_ai_artists.json` is loaded — `blocklists` should already be available in scope.

**Test cases:**
- Artist with 3 collaborators, none flagged → moderate green (unchanged behavior)
- Artist with 3 collaborators, 2 on AI blocklist → moderate red with tag `"pfc_network"`
- Artist with 5 related Deezer artists, 1 flagged → weak red for the flagged + green for the clean 4
- Artist with 0 collaborators → no collaboration evidence emitted (unchanged)

**Do NOT break:**
- The `Evidence` object structure — keep the same fields (finding, source, evidence_type, strength, tags)
- The decision tree — it reads evidence by type/strength/tags, not by string matching on findings
- Credit Network Evidence (Collector #9) — that's a separate collector checking shared producers/songwriters; do not merge them

---

### Task 1.3 — Widen Year Regex (L-3)

**Problem:** The year regex `19[5-9]\d|20[0-2]\d` misses years before 1950 and after 2029. This silently drops date evidence for classical crossover artists, jazz legends, or anything referencing older dates.

**Locate:**
```bash
grep -rn '19\[5-9\]' --include="*.py"
grep -rn '20\[0-2\]' --include="*.py"
# Also try the raw pattern:
grep -rn "year" --include="*.py" | grep -i "regex\|pattern\|re\.\|compile"
```

**What to change:**

Find the regex pattern and replace:
```python
# OLD:
r'19[5-9]\d|20[0-2]\d'

# NEW:
r'(?:19|20)\d{2}'
```

Then, wherever the match result is used, add a bounds check:
```python
from datetime import datetime

def _parse_year(year_str: str) -> int | None:
    """Parse and validate a year string."""
    try:
        year = int(year_str)
        current_year = datetime.now().year
        if 1900 <= year <= current_year + 2:
            return year
    except (ValueError, TypeError):
        pass
    return None
```

Apply `_parse_year()` everywhere the year regex match is consumed. Search for all usages:
```bash
grep -rn "year_match\|year_str\|found_year\|release_year\|begin_date" --include="*.py"
```

**Test cases:**
- `"1947"` → 1947 (was silently skipped before)
- `"2031"` → 2031 if current year is 2029+, else None
- `"2099"` → None (out of bounds)
- `"1899"` → None (out of bounds)
- `"2024"` → 2024 (unchanged, still works)
- `"1955"` → 1955 (unchanged, still works)

---

## Sprint 2: Reliability & Resilience

---

### Task 2.1 — Two-Pass Future Drain in audit_runner.py (M-20)

**Problem:** When the timeout fires, the drain loop iterates over futures and marks non-`.done()` futures as skipped. But a future might complete *during* the iteration (race condition), so valid results get discarded.

The existing H-6 mitigation (a drain loop) partially helps but doesn't catch futures that complete between the `.done()` check and the skip marking.

**Locate:**
```bash
grep -rn "done()\|as_completed\|timeout\|drain\|skipped" --include="*.py" | grep -i "audit\|runner\|future"
# Or find the file directly:
find . -name "audit_runner.py" -o -name "*runner*" | grep -v __pycache__
```

Look for a pattern like:
```python
for future in futures:
    if future.done():
        results.append(future.result())
    else:
        skipped.append(...)
```

**What to change:**

Replace the single-pass drain with a two-pass approach:

```python
def drain_futures(futures: dict, timeout_seconds: float = None) -> tuple[list, list]:
    """Drain completed futures with an optional timeout.
    
    Returns (results, skipped_names) using a two-pass approach
    to avoid the race condition where a future completes between
    the .done() check and the skip marking.
    
    Args:
        futures: dict mapping artist_name → Future
        timeout_seconds: optional wait time before draining
    """
    import time
    
    results = []
    skipped_names = []
    
    if timeout_seconds:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if all(f.done() for f in futures.values()):
                break
            time.sleep(0.1)
    
    # PASS 1: Collect ALL completed futures right now
    completed = {}
    still_running = {}
    for artist_name, future in futures.items():
        if future.done():
            completed[artist_name] = future
        else:
            still_running[artist_name] = future
    
    # Process completed results
    for artist_name, future in completed.items():
        try:
            result = future.result()
            results.append(result)
        except Exception as e:
            results.append({
                "artist_name": artist_name,
                "status": "error",
                "error": str(e),
            })
    
    # PASS 2: Re-check the ones that were still running
    # Some may have completed between pass 1 and now
    for artist_name, future in still_running.items():
        if future.done():
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({
                    "artist_name": artist_name,
                    "status": "error",
                    "error": str(e),
                })
        else:
            skipped_names.append(artist_name)
            future.cancel()
    
    return results, skipped_names
```

**Wire it in:** Replace the existing drain/skip loop with a call to `drain_futures()`. The function signature may need adjusting to match how futures are currently stored (dict vs list). Check the existing code:
```bash
grep -B5 -A20 "done()" audit_runner.py
```

**Do NOT break:**
- The result format expected by downstream code — `results` should contain the same objects as before
- The skip tracking — `skipped_names` should feed into whatever displays "N artists timed out" in the UI
- Any logging that counts completed vs skipped artists

---

### Task 2.2 — Add Rate Limiting to /api/scan (M-25)

**Problem:** No per-IP rate limiting on the scan endpoint. Each scan triggers 5-7 API calls per artist × N artists per playlist. Without limiting, one user (or attacker) can exhaust external API quotas.

**Locate the Flask app:**
```bash
grep -rn "@app.route.*scan\|api/scan" --include="*.py"
grep -rn "Flask(__name__)" --include="*.py"
```

**What to change:**

1. **Add dependency:**
```bash
pip install Flask-Limiter
```
Add `Flask-Limiter>=3.0` to `requirements.txt`.

2. **Initialize the limiter** in the Flask app file:

```python
from flask_limiter import Limiter
from flask import request

def get_real_ip():
    """Get real client IP, even behind reverse proxy (Render, Railway, etc.)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr

limiter = Limiter(
    key_func=get_real_ip,
    app=app,
    default_limits=[],        # No global limit — only on specific endpoints
    storage_uri="memory://",  # In-memory store; swap to redis:// for multi-process
)
```

3. **Apply to the scan endpoint:**

```python
@app.route("/api/scan", methods=["POST"])
@limiter.limit("5/minute")
def scan_playlist():
    # ... existing scan logic unchanged ...
```

4. **Add a 429 error handler:**

```python
@app.errorhandler(429)
def rate_limit_exceeded(e):
    return {
        "error": "Rate limit exceeded",
        "message": "Maximum 5 scans per minute. Please wait before trying again.",
        "retry_after": e.description,
    }, 429
```

5. **Frontend handling** — find where the React app calls `/api/scan` and add 429 handling:
```bash
grep -rn "api/scan\|fetch.*scan\|axios.*scan" --include="*.js" --include="*.jsx" --include="*.ts" --include="*.tsx"
```

Add a user-friendly message when 429 is received instead of a generic error.

**Test:** `curl` the endpoint 6 times rapidly:
```bash
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/api/scan -H "Content-Type: application/json" -d '{"url":"https://open.spotify.com/playlist/test"}'
done
# Expected: 200 200 200 200 200 429
```

---

## Sprint 3: Code Hygiene & Efficiency

---

### Task 3.1 — Remove Redundant `_collect_platform_evidence()` Call (L-5)

**Problem:** `_collect_platform_evidence()` is called early in `evaluate_artist()`, its result is stored in a variable, then that variable is never used. ~400 lines later, the same function is called again with fully populated data. The first call is wasted computation.

**Locate:**
```bash
grep -rn "_collect_platform_evidence" --include="*.py"
```

You should see two call sites in `evaluate_artist()` (or equivalent function). One early, one late.

**What to change:**
1. Delete the first call and its variable assignment
2. Verify the second call runs after all platform lookups (Deezer, MusicBrainz, Genius, etc.) have completed
3. Run a full scan comparison before and after to ensure identical output

**Verify correctness:**
```bash
# Before the change, run a scan and save output:
python -c "..." > /tmp/before.json

# Make the change, run the same scan:
python -c "..." > /tmp/after.json

# Diff:
diff /tmp/before.json /tmp/after.json
# Should be empty (identical output)
```

---

### Task 3.2 — Rename `likely_non_authentic` → `inconclusive` (L-11)

**Problem:** The verdict breakdown dict uses `"likely_non_authentic"` as the key for "Inconclusive" artists. This is confusing — the label sounds negative but the verdict is neutral.

**Find all occurrences:**
```bash
grep -rn "likely_non_authentic" --include="*.py" --include="*.js" --include="*.jsx" --include="*.ts" --include="*.tsx" --include="*.json"
```

**What to change:**

1. **Backend** — wherever the breakdown dict is constructed:
```python
# OLD:
"likely_non_authentic": inconclusive_count
# NEW:
"inconclusive": inconclusive_count
```

2. **Frontend** — update all JavaScript/React references to the key

3. **Backward compatibility** — if there's stored JSON in SQLite (`verdict_breakdown` column on the `scans` table), add a compatibility shim wherever the breakdown is read:
```python
inconclusive = breakdown.get("inconclusive", breakdown.get("likely_non_authentic", 0))
```

4. **Frontend compatibility** — same pattern:
```javascript
const inconclusive = breakdown.inconclusive ?? breakdown.likely_non_authentic ?? 0;
```

---

### Task 3.3 — Archive Orphaned Modules (L-15)

**Problem:** Three modules have zero imports anywhere in the codebase:
- `analyze_pfc_data.py`
- `fetch_pfc_tracks.py`
- `api_logger.py`

**Verify they're dead:**
```bash
grep -rn "import analyze_pfc_data\|from analyze_pfc_data\|import fetch_pfc_tracks\|from fetch_pfc_tracks\|import api_logger\|from api_logger" --include="*.py"
# Also check dynamic usage:
grep -rn "analyze_pfc_data\|fetch_pfc_tracks\|api_logger" --include="*.py" | grep -v "^.*analyze_pfc_data.py\|^.*fetch_pfc_tracks.py\|^.*api_logger.py"
```

If zero hits, they're safe to move.

**Find their actual paths:**
```bash
find . -name "analyze_pfc_data.py" -o -name "fetch_pfc_tracks.py" -o -name "api_logger.py" | grep -v __pycache__
```

**Execute:**
```bash
mkdir -p scripts/archive
# Move files (adjust paths based on find results):
mv <path>/analyze_pfc_data.py scripts/archive/
mv <path>/fetch_pfc_tracks.py scripts/archive/
mv <path>/api_logger.py scripts/archive/

cat > scripts/archive/README.md << 'EOF'
# Archived Modules

Removed from the main package — zero imports found anywhere in the codebase.
May contain useful reference logic. Extract what you need rather than re-importing.

- `analyze_pfc_data.py` — early PFC data analysis prototype
- `fetch_pfc_tracks.py` — early track fetching prototype  
- `api_logger.py` — unused API logging module
EOF
```

---

## Deferred — No Action Required

Documented for completeness. Revisit if circumstances change.

| Issue | Status | Why Deferred |
|---|---|---|
| M-16: Unsynchronized rate limiters | Mitigated | Per-call delays + API-side 429 handling already in place |
| M-19: Retry doesn't reuse cache | Mitigated | Main path caching covers most re-scans; retry is rare |
| L-1: 8 unused ExternalData fields | Cosmetic | No runtime impact — just unused struct fields |
| L-4: Year-only dates → false same-day flag | Edge case | Affects a weak-strength flag only |
| L-7: YouTube topic channels flagged | Rare | Auto-generated channels are rare in practice |
| L-8: "Not on Blocklist" ignores songwriter match | Neutral | Neutral evidence, doesn't affect scoring |
| L-9: Score boundary values unreachable | Cosmetic | Scores 0–1 and 99–100 never produced |
| L-10: Secondary sort counter-intuitive | Visual | Doesn't affect verdicts |
| L-19: Unnecessary getattr() | Style | No impact |
| L-20: .env.example placeholder | Hygiene | Not a vulnerability |

---

## Implementation Checklist

```
Sprint 1: Detection Accuracy
- [ ] 1.1: Grep for extract_primary_artist and _ARTIST_SEPARATORS to locate files
- [ ] 1.1: Replace extract_primary_artist() with two-phase approach
- [ ] 1.1: Add _AND_BANDS allowlist + " and the " pattern detection  
- [ ] 1.1: Add _FEATURED_PATTERN regex for parenthetical (feat./ft./with) stripping
- [ ] 1.1: Add _PROD_PATTERN regex for prod. credit stripping
- [ ] 1.1: Add lookup_artist_with_fallback() wrapper function
- [ ] 1.1: Wire fallback into all API lookup call sites (grep for all callers)
- [ ] 1.1: Test with all edge cases in the test table above
- [ ] 1.2: Grep for _collect_collaboration_evidence to locate file
- [ ] 1.2: Add blocklists + entity_db params to function signature
- [ ] 1.2: Add cross-reference logic against ai_artists, pfc_songwriters, entity DB
- [ ] 1.2: Modify green flag emission: only award when collaborators are clean
- [ ] 1.2: Add red flag with tag "pfc_network" when collaborators are flagged
- [ ] 1.2: Update caller in evaluate_artist() to pass blocklists and entity_db
- [ ] 1.3: Grep for year regex pattern to locate file
- [ ] 1.3: Widen regex from 19[5-9]\d|20[0-2]\d to (19|20)\d{2}
- [ ] 1.3: Add _parse_year() bounds check at all consumption points
- [ ] 1.3: Test with pre-1950 and post-2029 dates

Sprint 2: Reliability & Resilience  
- [ ] 2.1: Locate the future drain loop in audit_runner (or equivalent)
- [ ] 2.1: Replace single-pass drain with two-pass drain_futures()
- [ ] 2.1: Verify pass 2 re-checks futures that were running during pass 1
- [ ] 2.2: pip install Flask-Limiter, add to requirements.txt
- [ ] 2.2: Initialize limiter with get_real_ip() key function
- [ ] 2.2: Apply @limiter.limit("5/minute") to /api/scan endpoint
- [ ] 2.2: Add 429 error handler with retry message
- [ ] 2.2: Update frontend to handle 429 gracefully
- [ ] 2.2: Test: 6 rapid requests → 6th returns 429

Sprint 3: Code Hygiene
- [ ] 3.1: Grep for _collect_platform_evidence — find the two call sites
- [ ] 3.1: Delete the first (redundant) call
- [ ] 3.1: Verify scan output is identical before and after
- [ ] 3.2: Grep for likely_non_authentic across all file types
- [ ] 3.2: Rename to inconclusive in backend
- [ ] 3.2: Update frontend references
- [ ] 3.2: Add backward compatibility shim for stored JSON
- [ ] 3.3: Verify 3 orphaned modules have zero imports
- [ ] 3.3: Move to scripts/archive/ with README
```
