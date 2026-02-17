"""
Claude-powered deep analysis for artist authenticity evaluation.

Uses Anthropic's Claude API to analyze:
- Artist bios for AI-generated text patterns, unverifiable claims
- Profile images for AI generation artifacts
- Overall synthesis of all available data

Requires ANTHROPIC_API_KEY in .env.
"""

from __future__ import annotations

import base64
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests
from anthropic import Anthropic

from spotify_audit.spotify_client import ArtistInfo
from spotify_audit.evidence import ExternalData, Evidence

logger = logging.getLogger(__name__)

# Timeout for image download
IMAGE_TIMEOUT = 10
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB


@dataclass
class DeepAnalysis:
    """Results from Claude-powered deep analysis."""
    bio_analysis: list[Evidence] = field(default_factory=list)
    image_analysis: list[Evidence] = field(default_factory=list)
    synthesis: list[Evidence] = field(default_factory=list)
    raw_bio_response: str = ""
    raw_image_response: str = ""
    raw_synthesis_response: str = ""


def _fetch_image_base64(url: str) -> tuple[str, str] | None:
    """Download an image and return (base64_data, media_type) or None."""
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=IMAGE_TIMEOUT, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg")
        # Normalize media type
        if "png" in content_type:
            media_type = "image/png"
        elif "webp" in content_type:
            media_type = "image/webp"
        elif "gif" in content_type:
            media_type = "image/gif"
        else:
            media_type = "image/jpeg"

        data = resp.content
        if len(data) > MAX_IMAGE_BYTES:
            logger.debug("Image too large (%d bytes), skipping", len(data))
            return None
        return base64.standard_b64encode(data).decode("utf-8"), media_type
    except Exception as exc:
        logger.debug("Failed to fetch image from %s: %s", url, exc)
        return None


def _collect_bio_texts(artist: ArtistInfo, ext: ExternalData) -> str:
    """Gather all available bio/description text into a single string."""
    parts: list[str] = []
    if artist.bio:
        parts.append(f"Spotify bio: {artist.bio}")
    if ext.genius_description:
        parts.append(f"Genius description: {ext.genius_description}")
    if ext.discogs_profile:
        parts.append(f"Discogs profile: {ext.discogs_profile}")
    return "\n\n".join(parts)


def _build_artist_context(artist: ArtistInfo, ext: ExternalData) -> str:
    """Build a context string summarizing what we know about the artist."""
    lines = [f"Artist name: {artist.name}"]
    if artist.genres:
        lines.append(f"Genres: {', '.join(artist.genres)}")
    if artist.followers:
        lines.append(f"Spotify followers: {artist.followers:,}")
    if artist.monthly_listeners:
        lines.append(f"Monthly listeners: {artist.monthly_listeners:,}")
    if artist.deezer_fans:
        lines.append(f"Deezer fans: {artist.deezer_fans:,}")
    if artist.album_count or artist.single_count:
        lines.append(f"Catalog: {artist.album_count} albums, {artist.single_count} singles")
    if artist.labels:
        lines.append(f"Labels: {', '.join(artist.labels[:5])}")
    if artist.track_titles:
        lines.append(f"Sample tracks: {', '.join(artist.track_titles[:8])}")
    if ext.discogs_realname:
        lines.append(f"Real name (Discogs): {ext.discogs_realname}")
    if ext.musicbrainz_country:
        lines.append(f"Country (MusicBrainz): {ext.musicbrainz_country}")
    if ext.setlistfm_total_shows:
        lines.append(f"Live shows: {ext.setlistfm_total_shows}")
    return "\n".join(lines)


def analyze_bio(
    client: Anthropic,
    artist: ArtistInfo,
    ext: ExternalData,
    model: str = "claude-sonnet-4-5-20250929",
) -> list[Evidence]:
    """Analyze artist bio text for authenticity signals using Claude."""
    bio_text = _collect_bio_texts(artist, ext)
    if not bio_text:
        return [Evidence(
            finding="No biographical text available",
            source="Bio analysis",
            evidence_type="neutral",
            strength="weak",
            detail="No bio text found on Spotify, Genius, or Discogs. "
                   "Many ghost artists have no biography at all.",
        )]

    context = _build_artist_context(artist, ext)

    prompt = f"""You are analyzing an artist's biographical text to determine if this is a real human artist or a fabricated/ghost/AI artist profile.

ARTIST DATA:
{context}

BIOGRAPHICAL TEXT TO ANALYZE:
{bio_text}

Analyze this bio for the following signals:

1. **AI/ghost mentions**: Does it explicitly mention AI, generated, algorithm, or similar? Does it say "created by" a platform?
2. **ChatGPT-style writing**: Generic, vague, overly polished language with no specific details. Phrases like "blending genres," "pushing boundaries," "unique soundscapes."
3. **Verifiable claims**: Does it mention specific cities, venues, collaborators, education, or life events that could be verified? Or is it entirely vague?
4. **Geographic specificity**: Does it mention where the artist is from? Real artists almost always have a hometown or country.
5. **Career timeline**: Does it reference specific years, albums, or career milestones? Or is it timeless/generic?
6. **Red flags**: Unusually short bio, only describes the music's mood/vibe (not the person), or reads like a playlist description rather than an artist bio.

Respond in this EXACT format (keep each line short and specific):
VERDICT: [AUTHENTIC|SUSPICIOUS|INCONCLUSIVE]
CONFIDENCE: [HIGH|MEDIUM|LOW]
AI_MENTIONED: [YES|NO]
GEOGRAPHIC_SPECIFICITY: [YES|NO]
VERIFIABLE_CLAIMS: [YES|NO]
REASONING: [2-3 sentences explaining your assessment in plain English]"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Claude bio analysis failed for '%s': %s", artist.name, exc)
        return [Evidence(
            finding="Bio analysis unavailable",
            source="Claude (bio)",
            evidence_type="neutral",
            strength="weak",
            detail=f"Claude API call failed: {exc}",
        )]

    return _parse_bio_response(text, bio_text)


def _parse_bio_response(response: str, bio_text: str) -> list[Evidence]:
    """Parse Claude's bio analysis response into Evidence objects."""
    evidence: list[Evidence] = []

    verdict = _extract_field(response, "VERDICT", "INCONCLUSIVE").upper()
    confidence = _extract_field(response, "CONFIDENCE", "LOW").upper()
    ai_mentioned = _extract_field(response, "AI_MENTIONED", "NO").upper() == "YES"
    geo_specific = _extract_field(response, "GEOGRAPHIC_SPECIFICITY", "NO").upper() == "YES"
    verifiable = _extract_field(response, "VERIFIABLE_CLAIMS", "NO").upper() == "YES"
    reasoning = _extract_field(response, "REASONING", "Analysis inconclusive.")

    strength = "strong" if confidence == "HIGH" else "moderate" if confidence == "MEDIUM" else "weak"

    if ai_mentioned:
        evidence.append(Evidence(
            finding="Bio explicitly mentions AI or algorithmic creation",
            source="Claude (bio)",
            evidence_type="red_flag",
            strength="strong",
            detail="The artist's biography explicitly references AI generation, "
                   "algorithms, or automated music creation. " + reasoning,
            tags=["ai_mentioned_bio"],
        ))

    if verdict == "SUSPICIOUS":
        evidence.append(Evidence(
            finding="Bio text has hallmarks of a fabricated profile",
            source="Claude (bio)",
            evidence_type="red_flag",
            strength=strength,
            detail=reasoning,
            tags=["suspicious_bio"],
        ))
    elif verdict == "AUTHENTIC":
        evidence.append(Evidence(
            finding="Bio text appears to describe a real artist",
            source="Claude (bio)",
            evidence_type="green_flag",
            strength=strength,
            detail=reasoning,
            tags=["authentic_bio"],
        ))
    else:
        evidence.append(Evidence(
            finding="Bio text analysis inconclusive",
            source="Claude (bio)",
            evidence_type="neutral",
            strength="weak",
            detail=reasoning,
        ))

    if geo_specific:
        evidence.append(Evidence(
            finding="Bio includes geographic details",
            source="Claude (bio)",
            evidence_type="green_flag",
            strength="weak",
            detail="Biography mentions specific locations, suggesting a real person "
                   "with verifiable roots.",
            tags=["geo_specific_bio"],
        ))
    elif bio_text and not geo_specific:
        evidence.append(Evidence(
            finding="Bio lacks any geographic specificity",
            source="Claude (bio)",
            evidence_type="red_flag",
            strength="weak",
            detail="Biography doesn't mention where the artist is from. "
                   "Real artists almost always reference their hometown or country.",
            tags=["no_geo_bio"],
        ))

    if verifiable:
        evidence.append(Evidence(
            finding="Bio contains verifiable claims",
            source="Claude (bio)",
            evidence_type="green_flag",
            strength="moderate",
            detail="Biography references specific events, collaborators, venues, or dates "
                   "that could be independently verified.",
            tags=["verifiable_claims"],
        ))

    return evidence


def analyze_image(
    client: Anthropic,
    artist: ArtistInfo,
    model: str = "claude-sonnet-4-5-20250929",
) -> list[Evidence]:
    """Analyze artist profile image for AI generation artifacts using Claude vision."""
    if not artist.image_url:
        return [Evidence(
            finding="No profile image available",
            source="Image analysis",
            evidence_type="neutral",
            strength="weak",
            detail="No profile image URL found. Some ghost artists have no image, "
                   "while others use AI-generated or stock photos.",
        )]

    img_data = _fetch_image_base64(artist.image_url)
    if not img_data:
        return [Evidence(
            finding="Could not download profile image",
            source="Image analysis",
            evidence_type="neutral",
            strength="weak",
            detail=f"Failed to download image from {artist.image_url}.",
        )]

    b64, media_type = img_data

    prompt = """Analyze this artist profile image for signs of AI generation or inauthenticity.

Look for:
1. **AI generation artifacts**: Warped fingers/hands, asymmetric features, melted text, inconsistent lighting, uncanny valley faces, blurred backgrounds with sharp foreground
2. **Stock photo indicators**: Watermarks, overly generic compositions, standard corporate portrait style
3. **Abstract/non-human images**: Solid colors, geometric patterns, generic landscapes, or mood imagery instead of an actual person or band
4. **Professional photo indicators**: Real concert photos, studio shots with natural imperfections, candid moments, band group photos

Respond in this EXACT format:
IMAGE_TYPE: [HUMAN_PHOTO|AI_GENERATED|STOCK_PHOTO|ABSTRACT_ART|LOGO|OTHER]
AI_ARTIFACTS_DETECTED: [YES|NO|UNCERTAIN]
CONFIDENCE: [HIGH|MEDIUM|LOW]
REASONING: [2-3 sentences explaining what you see]"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Claude image analysis failed for '%s': %s", artist.name, exc)
        return [Evidence(
            finding="Image analysis unavailable",
            source="Claude (image)",
            evidence_type="neutral",
            strength="weak",
            detail=f"Claude vision API call failed: {exc}",
        )]

    return _parse_image_response(text)


def _parse_image_response(response: str) -> list[Evidence]:
    """Parse Claude's image analysis response into Evidence objects."""
    evidence: list[Evidence] = []

    image_type = _extract_field(response, "IMAGE_TYPE", "OTHER").upper()
    ai_artifacts = _extract_field(response, "AI_ARTIFACTS_DETECTED", "UNCERTAIN").upper()
    confidence = _extract_field(response, "CONFIDENCE", "LOW").upper()
    reasoning = _extract_field(response, "REASONING", "Analysis inconclusive.")

    strength = "strong" if confidence == "HIGH" else "moderate" if confidence == "MEDIUM" else "weak"

    if ai_artifacts == "YES":
        evidence.append(Evidence(
            finding="AI generation artifacts detected in profile image",
            source="Claude (image)",
            evidence_type="red_flag",
            strength=strength,
            detail=reasoning,
            tags=["ai_image_artifacts"],
        ))
    elif image_type == "AI_GENERATED":
        evidence.append(Evidence(
            finding="Profile image appears AI-generated",
            source="Claude (image)",
            evidence_type="red_flag",
            strength=strength,
            detail=reasoning,
            tags=["ai_generated_image"],
        ))
    elif image_type in ("ABSTRACT_ART", "LOGO", "OTHER"):
        evidence.append(Evidence(
            finding=f"Profile image is {image_type.lower().replace('_', ' ')} (not a person)",
            source="Claude (image)",
            evidence_type="red_flag",
            strength="weak",
            detail="Profile uses abstract art, a logo, or non-human imagery instead of "
                   "a photo. While some real artists do this, it's more common with "
                   "fabricated profiles. " + reasoning,
            tags=["abstract_image"],
        ))
    elif image_type == "STOCK_PHOTO":
        evidence.append(Evidence(
            finding="Profile image appears to be a stock photo",
            source="Claude (image)",
            evidence_type="red_flag",
            strength="moderate",
            detail="Profile image looks like a stock photo rather than an authentic "
                   "artist photo. Ghost artists frequently use stock imagery. " + reasoning,
            tags=["stock_photo"],
        ))
    elif image_type == "HUMAN_PHOTO" and ai_artifacts == "NO":
        evidence.append(Evidence(
            finding="Profile image appears to be an authentic photo",
            source="Claude (image)",
            evidence_type="green_flag",
            strength=strength,
            detail=reasoning,
            tags=["authentic_photo"],
        ))
    else:
        evidence.append(Evidence(
            finding="Image analysis inconclusive",
            source="Claude (image)",
            evidence_type="neutral",
            strength="weak",
            detail=reasoning,
        ))

    return evidence


def _synthesize(
    client: Anthropic,
    artist: ArtistInfo,
    ext: ExternalData,
    bio_evidence: list[Evidence],
    image_evidence: list[Evidence],
    model: str = "claude-sonnet-4-5-20250929",
) -> list[Evidence]:
    """Final synthesis: combine all signals into a threat assessment."""
    context = _build_artist_context(artist, ext)

    # Summarize prior evidence
    prior_lines = []
    for ev in bio_evidence + image_evidence:
        flag = "RED" if ev.evidence_type == "red_flag" else ("GREEN" if ev.evidence_type == "green_flag" else "NEUTRAL")
        prior_lines.append(f"[{flag} - {ev.strength}] {ev.finding}: {ev.detail}")

    if not prior_lines:
        return []

    prompt = f"""You are a music industry analyst investigating whether this is a real artist or a fabricated/ghost/AI artist.

ARTIST DATA:
{context}

EVIDENCE COLLECTED SO FAR:
{chr(10).join(prior_lines)}

Based on ALL the evidence above, provide a final synthesis assessment.

Classify into one of these threat categories:
- PFC_GHOST: Fabricated artist identity created by a production company (like Epidemic Sound), music written by ghost producers under fake names
- AI_GENERATED: Music created primarily by AI tools, artist identity may not correspond to a real person
- LEGITIMATE: Real human artist with genuine creative output
- INCONCLUSIVE: Not enough evidence to determine

Respond in this EXACT format:
CATEGORY: [PFC_GHOST|AI_GENERATED|LEGITIMATE|INCONCLUSIVE]
CONFIDENCE: [HIGH|MEDIUM|LOW]
REASONING: [2-3 sentences summarizing the key evidence and your conclusion]"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
    except Exception as exc:
        logger.debug("Synthesis failed for %s: %s", artist.name, exc)
        return []

    evidence = []
    category = _extract_field(text, "CATEGORY", "INCONCLUSIVE").upper()
    confidence = _extract_field(text, "CONFIDENCE", "LOW").upper()
    reasoning = _extract_field(text, "REASONING", "")

    if category in ("PFC_GHOST", "AI_GENERATED"):
        synth_tag = "synth_pfc_ghost" if category == "PFC_GHOST" else "synth_ai_generated"
        evidence.append(Evidence(
            finding=f"Claude synthesis: {category.replace('_', ' ').title()}",
            source="Claude synthesis",
            evidence_type="red_flag",
            strength="strong" if confidence == "HIGH" else "moderate",
            detail=reasoning,
            tags=[synth_tag],
        ))
    elif category == "LEGITIMATE":
        evidence.append(Evidence(
            finding="Claude synthesis: Likely Legitimate",
            source="Claude synthesis",
            evidence_type="green_flag",
            strength="strong" if confidence == "HIGH" else "moderate",
            detail=reasoning,
            tags=["synth_legitimate"],
        ))
    else:
        evidence.append(Evidence(
            finding="Claude synthesis: Inconclusive",
            source="Claude synthesis",
            evidence_type="neutral",
            strength="weak",
            detail=reasoning,
        ))

    return evidence


def run_deep_analysis(
    client: Anthropic,
    artist: ArtistInfo,
    ext: ExternalData,
    model: str = "claude-sonnet-4-5-20250929",
) -> DeepAnalysis:
    """Run full deep analysis: bio + image + synthesis."""
    result = DeepAnalysis()

    # Bio analysis
    result.bio_analysis = analyze_bio(client, artist, ext, model=model)

    # Image analysis
    result.image_analysis = analyze_image(client, artist, model=model)

    # Synthesis: combine all signals
    result.synthesis = _synthesize(
        client, artist, ext,
        result.bio_analysis, result.image_analysis,
        model=model,
    )

    return result


# ---------------------------------------------------------------------------
# Batch deep analysis — reduces API calls by grouping artists
# ---------------------------------------------------------------------------

BATCH_SIZE = 8  # artists per Claude call (bio & synthesis)


def _batch_analyze_bios(
    client: Anthropic,
    batch: list[tuple[str, ArtistInfo, ExternalData]],
    model: str = "claude-sonnet-4-5-20250929",
) -> dict[str, list[Evidence]]:
    """Analyze bios for multiple artists in a single Claude call.

    Returns {artist_key: [Evidence, ...]} for each artist in the batch.
    """
    # Build combined prompt
    artist_sections = []
    keys_in_order: list[str] = []
    bios_present: dict[str, str] = {}

    for key, artist, ext in batch:
        bio_text = _collect_bio_texts(artist, ext)
        context = _build_artist_context(artist, ext)
        keys_in_order.append(key)
        bios_present[key] = bio_text

        if not bio_text:
            artist_sections.append(
                f"=== ARTIST [{key}]: {artist.name} ===\n"
                f"CONTEXT:\n{context}\n"
                f"BIO: [NO BIO AVAILABLE]"
            )
        else:
            artist_sections.append(
                f"=== ARTIST [{key}]: {artist.name} ===\n"
                f"CONTEXT:\n{context}\n"
                f"BIO:\n{bio_text}"
            )

    prompt = f"""You are analyzing multiple artists' biographical text to determine if each is a real human artist or a fabricated/ghost/AI artist profile.

For each artist, analyze:
1. AI/ghost mentions (explicit AI, algorithm, "created by" platform)
2. ChatGPT-style writing (generic, vague, "blending genres", "pushing boundaries")
3. Verifiable claims (specific cities, venues, collaborators, education)
4. Geographic specificity (hometown, country)
5. Career timeline (specific years, albums, milestones)
6. Red flags (unusually short, describes mood not person, reads like playlist description)

{chr(10).join(artist_sections)}

For EACH artist, respond in this EXACT format (one block per artist, in the same order):

=== ARTIST [key] ===
VERDICT: [AUTHENTIC|SUSPICIOUS|INCONCLUSIVE]
CONFIDENCE: [HIGH|MEDIUM|LOW]
AI_MENTIONED: [YES|NO]
GEOGRAPHIC_SPECIFICITY: [YES|NO]
VERIFIABLE_CLAIMS: [YES|NO]
REASONING: [2-3 sentences]"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=300 * len(batch),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Batch bio analysis failed: %s", exc)
        # Fall back to individual calls
        results: dict[str, list[Evidence]] = {}
        for key, artist, ext in batch:
            results[key] = analyze_bio(client, artist, ext, model=model)
        return results

    # Parse response — split by artist blocks
    results: dict[str, list[Evidence]] = {}
    for key in keys_in_order:
        # Find the section for this artist
        pattern = rf"===\s*ARTIST\s*\[{re.escape(key)}\]\s*===\s*(.*?)(?====\s*ARTIST|$)"
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            section = m.group(1).strip()
            bio_text = bios_present.get(key, "")
            results[key] = _parse_bio_response(section, bio_text)
        else:
            # Section not found — mark as neutral
            results[key] = [Evidence(
                finding="Bio analysis: could not parse batch response",
                source="Claude (bio)",
                evidence_type="neutral",
                strength="weak",
                detail="Artist section not found in batch response.",
            )]

    return results


def _batch_synthesize(
    client: Anthropic,
    batch: list[tuple[str, ArtistInfo, ExternalData, list[Evidence], list[Evidence]]],
    model: str = "claude-sonnet-4-5-20250929",
) -> dict[str, list[Evidence]]:
    """Run synthesis for multiple artists in a single Claude call.

    batch items: (key, artist, ext, bio_evidence, image_evidence)
    Returns {artist_key: [Evidence, ...]}.
    """
    artist_sections = []
    keys_in_order: list[str] = []

    for key, artist, ext, bio_ev, img_ev in batch:
        keys_in_order.append(key)
        context = _build_artist_context(artist, ext)

        prior_lines = []
        for ev in bio_ev + img_ev:
            flag = "RED" if ev.evidence_type == "red_flag" else (
                "GREEN" if ev.evidence_type == "green_flag" else "NEUTRAL"
            )
            prior_lines.append(f"[{flag} - {ev.strength}] {ev.finding}: {ev.detail}")

        evidence_block = chr(10).join(prior_lines) if prior_lines else "[No evidence collected]"
        artist_sections.append(
            f"=== ARTIST [{key}] ===\n"
            f"DATA:\n{context}\n"
            f"EVIDENCE:\n{evidence_block}"
        )

    prompt = f"""You are a music industry analyst investigating whether these artists are real or fabricated/ghost/AI artists.

For each artist, classify into one of these threat categories:
- PFC_GHOST: Fabricated identity by a production company, ghost producers under fake names
- AI_GENERATED: Music created primarily by AI tools, identity may not be real
- LEGITIMATE: Real human artist with genuine creative output
- INCONCLUSIVE: Not enough evidence to determine

{chr(10).join(artist_sections)}

For EACH artist, respond in this EXACT format (one block per artist, in the same order):

=== ARTIST [key] ===
CATEGORY: [PFC_GHOST|AI_GENERATED|LEGITIMATE|INCONCLUSIVE]
CONFIDENCE: [HIGH|MEDIUM|LOW]
REASONING: [2-3 sentences]"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=200 * len(batch),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
    except Exception as exc:
        logger.debug("Batch synthesis failed: %s", exc)
        # Fall back to individual
        results: dict[str, list[Evidence]] = {}
        for key, artist, ext, bio_ev, img_ev in batch:
            results[key] = _synthesize(client, artist, ext, bio_ev, img_ev, model=model)
        return results

    results: dict[str, list[Evidence]] = {}
    for key in keys_in_order:
        pattern = rf"===\s*ARTIST\s*\[{re.escape(key)}\]\s*===\s*(.*?)(?====\s*ARTIST|$)"
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            section = m.group(1).strip()
            category = _extract_field(section, "CATEGORY", "INCONCLUSIVE").upper()
            confidence = _extract_field(section, "CONFIDENCE", "LOW").upper()
            reasoning = _extract_field(section, "REASONING", "")

            evidence = []
            if category in ("PFC_GHOST", "AI_GENERATED"):
                synth_tag = "synth_pfc_ghost" if category == "PFC_GHOST" else "synth_ai_generated"
                evidence.append(Evidence(
                    finding=f"Claude synthesis: {category.replace('_', ' ').title()}",
                    source="Claude synthesis",
                    evidence_type="red_flag",
                    strength="strong" if confidence == "HIGH" else "moderate",
                    detail=reasoning,
                    tags=[synth_tag],
                ))
            elif category == "LEGITIMATE":
                evidence.append(Evidence(
                    finding="Claude synthesis: Likely Legitimate",
                    source="Claude synthesis",
                    evidence_type="green_flag",
                    strength="strong" if confidence == "HIGH" else "moderate",
                    detail=reasoning,
                    tags=["synth_legitimate"],
                ))
            else:
                evidence.append(Evidence(
                    finding="Claude synthesis: Inconclusive",
                    source="Claude synthesis",
                    evidence_type="neutral",
                    strength="weak",
                    detail=reasoning,
                ))
            results[key] = evidence
        else:
            results[key] = []

    return results


def run_deep_analysis_batch(
    client: Anthropic,
    artists: list[tuple[str, ArtistInfo, ExternalData]],
    model: str = "claude-sonnet-4-5-20250929",
    on_progress: "callable | None" = None,
) -> dict[str, DeepAnalysis]:
    """Run deep analysis for multiple artists, batching bio and synthesis calls.

    Bio analysis: batched (BATCH_SIZE artists per call)
    Image analysis: individual (each image is unique + large)
    Synthesis: batched (BATCH_SIZE artists per call)

    Args:
        client: Anthropic client
        artists: list of (key, ArtistInfo, ExternalData) tuples
        model: Claude model to use
        on_progress: optional callback called after each artist completes

    Returns:
        {key: DeepAnalysis} for each artist
    """
    results: dict[str, DeepAnalysis] = {key: DeepAnalysis() for key, _, _ in artists}

    # Phase 1: Batch bio analysis
    for i in range(0, len(artists), BATCH_SIZE):
        batch = artists[i:i + BATCH_SIZE]
        bio_results = _batch_analyze_bios(client, batch, model=model)
        for key, evidence in bio_results.items():
            results[key].bio_analysis = evidence

    # Phase 2: Parallel image analysis (images too large to batch in one API call,
    # but downloads + individual Claude calls can overlap)
    def _analyze_one_image(item: tuple[str, ArtistInfo, ExternalData]) -> tuple[str, list]:
        key, artist, ext = item
        return key, analyze_image(client, artist, model=model)

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="img") as pool:
        futures = {pool.submit(_analyze_one_image, item): item[0] for item in artists}
        for fut in as_completed(futures):
            key, img_evidence = fut.result()
            results[key].image_analysis = img_evidence
            if on_progress:
                on_progress()

    # Phase 3: Batch synthesis
    for i in range(0, len(artists), BATCH_SIZE):
        batch_items = []
        for key, artist, ext in artists[i:i + BATCH_SIZE]:
            r = results[key]
            batch_items.append((key, artist, ext, r.bio_analysis, r.image_analysis))
        synth_results = _batch_synthesize(client, batch_items, model=model)
        for key, evidence in synth_results.items():
            results[key].synthesis = evidence

    return results


def _extract_field(text: str, field_name: str, default: str = "") -> str:
    """Extract a field value from structured Claude response."""
    pattern = rf"{field_name}:\s*(.+?)(?:\n|$)"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("[]")
    return default
