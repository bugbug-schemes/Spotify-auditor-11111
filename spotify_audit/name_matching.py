"""
Shared artist name matching module.

All API clients use this module for name normalization, candidate generation,
similarity scoring, and disambiguation. No client should do its own ad-hoc
string comparison.

The goal: maximize true matches so that when we report "not found," we have
high confidence it's genuinely absent, not a matching failure.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from thefuzz import fuzz
from unidecode import unidecode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Match result
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Result of matching a query against API search results."""
    found: bool                          # Did we find a match?
    confidence: float = 0.0              # 0.0 to 1.0
    matched_name: str | None = None      # Name as it appears on the platform
    platform_id: str | None = None       # Platform-specific ID
    match_method: str = ""               # "exact", "normalized", "alias", "fuzzy", "platform_id"
    ambiguous: bool = False              # True if multiple plausible matches
    candidates: list[dict] = field(default_factory=list)  # All scored candidates


# ---------------------------------------------------------------------------
# Step 1: Normalization pipeline
# ---------------------------------------------------------------------------

def strip_accents(s: str) -> str:
    """Remove diacritical marks: e→e, o→o, n→n, etc."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


SYMBOL_MAP = {
    "$": "s",
    "!": "i",
    "@": "a",
    "+": "and",
    "&": "and",
}


def replace_symbols(s: str) -> str:
    """Replace stylistic symbols with letter equivalents."""
    for symbol, replacement in SYMBOL_MAP.items():
        s = s.replace(symbol, replacement)
    return s


def strip_punctuation(s: str) -> str:
    """Remove periods, commas, hyphens, apostrophes, quotes, parentheses."""
    s = re.sub(r"[-\u2013\u2014]", " ", s)  # hyphens/dashes → space
    s = re.sub(r"[.,;:!?'\"()\[\]{}/\\]", "", s)
    return s


def normalize_whitespace(s: str) -> str:
    """Collapse all whitespace variants to single spaces, strip edges."""
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_name(raw: str) -> str:
    """Standard normalization applied to all names before comparison.

    Pipeline: NFC → casefold → strip accents → strip punctuation →
    normalize whitespace.
    """
    s = unicodedata.normalize("NFC", raw)
    s = s.casefold()
    s = strip_accents(s)
    s = strip_punctuation(s)
    s = normalize_whitespace(s)
    return s


def transliterate_name(name: str) -> str:
    """Convert non-Latin characters to ASCII approximation."""
    return unidecode(name)


def has_non_latin(name: str) -> bool:
    """Check if name contains non-Latin characters."""
    return any(ord(c) > 127 for c in name)


# ---------------------------------------------------------------------------
# Step 2: Generate search candidates
# ---------------------------------------------------------------------------

PREFIXES = [
    "the ", "dj ", "mc ", "dr. ", "dr ", "mr. ", "mr ",
    "mrs. ", "mrs ", "lil ", "lil' ", "big ", "young ",
]

SEPARATORS = [
    " & ", " and ", " x ", " vs ", " vs. ",
    " feat ", " feat. ", " ft ", " ft. ", " with ",
]


def generate_candidates(raw_name: str) -> list[str]:
    """Generate search variants ordered from most to least specific."""
    candidates: list[str] = []

    # 1. Original name
    candidates.append(raw_name)

    # 2. Normalized form
    norm = normalize_name(raw_name)
    candidates.append(norm)

    # 3. Symbol-replaced form
    sym_replaced = normalize_name(replace_symbols(raw_name))
    if sym_replaced != norm:
        candidates.append(sym_replaced)

    # 4. Without common prefixes
    for prefix in PREFIXES:
        if norm.startswith(prefix):
            candidates.append(norm[len(prefix):])
            break

    # 5. With "the" added
    if not norm.startswith("the "):
        candidates.append("the " + norm)

    # 6. Collaboration splits
    for sep in SEPARATORS:
        if sep in norm:
            parts = norm.split(sep)
            candidates.extend(p.strip() for p in parts if p.strip())
            break

    # 7. Name reversal for "First Last" → "Last, First" (ASCAP/BMI)
    words = norm.split()
    if len(words) == 2:
        candidates.append(f"{words[1]}, {words[0]}")
        candidates.append(f"{words[1]} {words[0]}")

    # 8. Transliteration for non-Latin names
    if has_non_latin(raw_name):
        trans = transliterate_name(raw_name)
        trans_norm = normalize_name(trans)
        if trans_norm != norm:
            candidates.append(trans_norm)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        c_clean = c.strip()
        if c_clean and c_clean not in seen:
            seen.add(c_clean)
            unique.append(c_clean)

    return unique


# ---------------------------------------------------------------------------
# Step 3: Similarity scoring
# ---------------------------------------------------------------------------

def _raw_similarity(q: str, c: str) -> float:
    """Compute weighted fuzzy similarity between two already-normalized strings."""
    if q == c:
        return 1.0
    if not q or not c:
        return 0.0
    token_sort = fuzz.token_sort_ratio(q, c) / 100.0
    token_set = fuzz.token_set_ratio(q, c) / 100.0
    partial = fuzz.partial_ratio(q, c) / 100.0
    return (token_sort * 0.4) + (token_set * 0.35) + (partial * 0.25)


def similarity_score(query: str, candidate: str) -> float:
    """Compare a search query to an API result name.

    Returns 0.0 to 1.0.  Tries both plain normalization and symbol-replaced
    normalization, returning the higher score.  This ensures "Ke$ha" matches
    "Kesha" and "P!nk" matches "Pink".
    """
    q_plain = normalize_name(query)
    c_plain = normalize_name(candidate)

    score_plain = _raw_similarity(q_plain, c_plain)

    # Also try with symbol replacement applied to both sides
    q_sym = normalize_name(replace_symbols(query))
    c_sym = normalize_name(replace_symbols(candidate))

    score_sym = _raw_similarity(q_sym, c_sym)

    # Cross-compare: symbol-replaced query vs plain candidate and vice versa
    score_cross1 = _raw_similarity(q_sym, c_plain)
    score_cross2 = _raw_similarity(q_plain, c_sym)

    return max(score_plain, score_sym, score_cross1, score_cross2)


def min_confidence_for_length(name: str) -> float:
    """Shorter names need higher similarity scores to be trusted."""
    length = len(normalize_name(name))
    if length <= 3:
        return 0.99
    elif length <= 6:
        return 0.95
    elif length <= 10:
        return 0.88
    elif length <= 20:
        return 0.82
    else:
        return 0.75


# ---------------------------------------------------------------------------
# Step 4: Disambiguation and match picking
# ---------------------------------------------------------------------------

def pick_best_match(
    query: str,
    candidates: list[dict],
    context: dict | None = None,
) -> MatchResult:
    """From a list of API results, pick the best match.

    Each candidate dict must have a "name" key.  May also have:
    "id", "genres", "country", "aliases", "listeners", "nb_fan".

    context dict may contain: genres, country, begin_year, monthly_listeners
    """
    if context is None:
        context = {}

    scored: list[tuple[float, dict]] = []

    for cand in candidates:
        cand_name = cand.get("name", "")
        name_score = similarity_score(query, cand_name)

        # Also check aliases
        best_alias_score = 0.0
        for alias in cand.get("aliases", []):
            alias_score = similarity_score(query, alias)
            if alias_score > best_alias_score:
                best_alias_score = alias_score

        # Use whichever is higher
        if best_alias_score > name_score:
            name_score = best_alias_score

        # Genre overlap bonus
        genre_bonus = 0.0
        if context.get("genres") and cand.get("genres"):
            ctx_genres = {g.lower() for g in context["genres"]}
            cand_genres = {g.lower() for g in cand["genres"]}
            overlap = ctx_genres & cand_genres
            if overlap:
                genre_bonus = min(len(overlap) * 0.03, 0.1)

        # Country match bonus
        country_bonus = 0.0
        if context.get("country") and cand.get("country"):
            if context["country"].lower() == cand["country"].lower():
                country_bonus = 0.05

        total = name_score + genre_bonus + country_bonus
        scored.append((total, cand))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return MatchResult(found=False, confidence=0.0)

    best_score, best_candidate = scored[0]
    min_conf = min_confidence_for_length(query)

    # Check if ambiguous
    ambiguous = (
        len(scored) >= 2
        and scored[0][0] - scored[1][0] < 0.05
        and scored[1][0] > min_conf
    )

    # Build scored candidates list for debugging
    cand_list = [
        {"name": c.get("name", ""), "id": c.get("id"), "score": round(s, 4)}
        for s, c in scored[:5]
    ]

    if best_score >= min_conf:
        method = "exact" if best_score >= 1.0 else (
            "normalized" if best_score >= 0.98 else "fuzzy"
        )
        return MatchResult(
            found=True,
            confidence=min(best_score, 1.0),
            matched_name=best_candidate.get("name"),
            platform_id=str(best_candidate.get("id", "")) if best_candidate.get("id") else None,
            match_method=method,
            ambiguous=ambiguous,
            candidates=cand_list,
        )
    else:
        return MatchResult(
            found=False,
            confidence=best_score,
            matched_name=best_candidate.get("name"),
            ambiguous=ambiguous,
            candidates=cand_list,
        )


# ---------------------------------------------------------------------------
# Step 5: MusicBrainz platform ID extraction
# ---------------------------------------------------------------------------

def get_platform_ids_from_musicbrainz(urls: dict[str, str]) -> dict[str, str]:
    """Extract platform-specific IDs from MusicBrainz URL relationships.

    Args:
        urls: Dict of {relation_type: url} from MusicBrainz enrich.

    Returns:
        Dict like {"discogs": "12345", "genius": "67890", "lastfm": "Artist+Name", ...}
    """
    platform_ids: dict[str, str] = {}

    for _rel_type, url in urls.items():
        url_lower = url.lower()

        if "discogs.com/artist/" in url:
            # https://www.discogs.com/artist/12345-Name → "12345"
            segment = url.split("/artist/")[-1]
            # Discogs IDs may have "-Name" suffix
            discogs_id = segment.split("-")[0]
            if discogs_id.isdigit():
                platform_ids["discogs"] = discogs_id

        elif "genius.com/artists/" in url:
            platform_ids["genius"] = url.split("/artists/")[-1]

        elif "last.fm/music/" in url_lower:
            segment = url.split("/music/")[-1]
            platform_ids["lastfm"] = segment.replace("+", " ")

        elif "youtube.com/channel/" in url_lower:
            platform_ids["youtube"] = url.split("/channel/")[-1]

        elif "bandcamp.com" in url_lower:
            platform_ids["bandcamp"] = url

        elif "setlist.fm" in url_lower:
            platform_ids["setlistfm"] = url

        elif "wikidata.org" in url_lower:
            platform_ids["wikidata"] = url.split("/")[-1]

        elif "songkick.com/artists/" in url_lower:
            segment = url.split("/artists/")[-1]
            sk_id = segment.split("-")[0]
            if sk_id.isdigit():
                platform_ids["songkick"] = sk_id

        elif "wikipedia.org/wiki/" in url_lower:
            platform_ids["wikipedia"] = url.split("/wiki/")[-1]

    return platform_ids


# ---------------------------------------------------------------------------
# Step 7: Match quality logging
# ---------------------------------------------------------------------------

def log_match(platform: str, query: str, result: MatchResult) -> None:
    """Log match quality for debugging and threshold tuning."""
    if result.found:
        logger.info(
            "[%s] MATCHED '%s' → '%s' (confidence=%.3f, method=%s, ambiguous=%s)",
            platform, query, result.matched_name,
            result.confidence, result.match_method, result.ambiguous,
        )
    else:
        logger.info(
            "[%s] NOT FOUND '%s' (best_score=%.3f, candidates=%d)",
            platform, query, result.confidence, len(result.candidates),
        )


# ---------------------------------------------------------------------------
# Convenience: search with candidates
# ---------------------------------------------------------------------------

def search_with_candidates(
    query: str,
    search_fn,
    parse_fn,
    platform: str,
    context: dict | None = None,
    max_variants: int = 3,
) -> MatchResult:
    """Try multiple candidate queries against a search function.

    Args:
        query: Original artist name.
        search_fn: Callable(query_str) → list[dict] of raw API results.
        parse_fn: Callable(raw_result) → dict with at least "name" and "id" keys.
        platform: Platform name for logging.
        context: Disambiguation context (genres, country, etc.).
        max_variants: Max candidate variants to try.

    Returns:
        Best MatchResult found across all attempts.
    """
    candidates_list = generate_candidates(query)
    best_result = MatchResult(found=False, confidence=0.0)

    for variant in candidates_list[:max_variants]:
        try:
            raw_results = search_fn(variant)
        except Exception as exc:
            logger.debug("[%s] Search failed for '%s': %s", platform, variant, exc)
            continue

        if not raw_results:
            continue

        parsed = [parse_fn(r) for r in raw_results]
        result = pick_best_match(query, parsed, context)

        if result.found:
            log_match(platform, query, result)
            return result

        if result.confidence > best_result.confidence:
            best_result = result

    log_match(platform, query, best_result)
    return best_result
