"""
Report formatters: Markdown, HTML (with radar charts), and JSON output.

The HTML report produces:
- A summary dashboard with health gauge, verdict/threat bars, key metrics
- Per-artist expandable detail cards with SVG radar charts, evidence flags,
  metadata grids, and related entity connections
"""

from __future__ import annotations

import json
import math
import html as html_mod
from datetime import datetime, timezone

from spotify_audit.scoring import PlaylistReport, ArtistReport
from spotify_audit.evidence import ArtistEvaluation, Evidence, Verdict, ExternalData


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def to_json(report: PlaylistReport) -> str:
    """Serialize the full playlist report to JSON."""
    return json.dumps(_report_to_dict(report), indent=2, default=str)


def _report_to_dict(report: PlaylistReport) -> dict:
    not_scanned = len(report.skipped_artists) if report.skipped_artists else 0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "playlist": {
            "name": report.playlist_name,
            "id": report.playlist_id,
            "owner": report.owner,
            "total_tracks": report.total_tracks,
            "total_unique_artists": report.total_unique_artists,
            "is_spotify_owned": report.is_spotify_owned,
        },
        "summary": {
            "health_score": report.health_score,
            "analyzed_count": len(report.artists),
            "timed_out_count": not_scanned,
            "total_playlist_artists": report.total_unique_artists,
            "verdict_breakdown": {
                "Verified Artist": report.verified_artists,
                "Likely Authentic": report.likely_authentic,
                "Inconclusive": report.inconclusive,
                "Suspicious": report.suspicious,
                "Likely Artificial": report.likely_artificial,
                "Not Scanned": not_scanned,
            },
        },
        "artists": [_artist_to_dict(a) for a in report.artists],
    }


def _artist_to_dict(a: ArtistReport) -> dict:
    d: dict = {
        "artist_id": a.artist_id,
        "artist_name": a.artist_name,
        "verdict": a.verdict,
        "final_score": a.final_score,
    }

    # Threat category
    if a.threat_category is not None:
        d["threat_category"] = a.threat_category
        d["threat_category_name"] = a.threat_category_name

    # Evidence-based evaluation
    ev = a.evaluation
    if ev:
        d["confidence"] = ev.confidence
        d["matched_rule"] = getattr(ev, "matched_rule", "")
        d["platform_presence"] = ev.platform_presence.names()
        d["radar"] = {
            "labels": ["Platform Presence", "Fan Engagement", "Creative History",
                       "IRL Presence", "Industry Signals", "Blocklist Status"],
            "scores": list(ev.category_scores.values()),
        }
        d["category_scores"] = ev.category_scores
        d["decision_path"] = ev.decision_path
        d["red_flags"] = [_evidence_to_dict(e) for e in ev.red_flags]
        d["green_flags"] = [_evidence_to_dict(e) for e in ev.green_flags]
        d["neutral_notes"] = [_evidence_to_dict(e) for e in ev.neutral_notes]
        if ev.labels:
            d["labels"] = ev.labels
        if ev.contributors:
            d["contributors"] = ev.contributors

        # Per-artist API status (Fix 6) — found / not_found / error / timeout / skipped
        ext = ev.external_data
        if ext:
            d["api_status"] = _build_api_status(ext)
            d["profile_urls"] = _build_profile_urls(ext)
            d["bio_data"] = _build_bio_data(ext)
            d["musicbrainz_relationship_count"] = getattr(ext, "musicbrainz_relationship_count", 0)
            d["deezer_track_ranks"] = getattr(ext, "deezer_track_ranks", [])
            d["external_data"] = _external_data_to_dict(ext)

    # Legacy score data (supplementary)
    if a.quick_score is not None:
        d["legacy_quick_score"] = a.quick_score
    if a.standard_score is not None:
        d["legacy_standard_score"] = a.standard_score
    if a.quick_signals:
        d["quick_signals"] = a.quick_signals

    return d


def _build_api_status(ext: ExternalData) -> dict:
    """Build per-platform API status: found / not_found / error / skipped."""
    platforms = {
        "deezer": ext.genius_found is not None,  # always checked
        "musicbrainz": True,
        "genius": True,
        "lastfm": True,
        "discogs": True,
        "setlistfm": True,
        "wikipedia": True,
    }
    status: dict[str, str] = {}
    found_map = {
        "deezer": True,  # always resolved via Deezer
        "musicbrainz": ext.musicbrainz_found,
        "genius": ext.genius_found,
        "lastfm": ext.lastfm_found,
        "discogs": ext.discogs_found,
        "setlistfm": ext.setlistfm_found,
        "wikipedia": ext.wikipedia_found,
    }
    errors_lower = {k.lower(): v for k, v in ext.api_errors.items()}
    for platform in found_map:
        if found_map[platform]:
            status[platform] = "found"
        elif platform in errors_lower or platform.replace(".", "") in {
            k.replace(".", "").replace(" ", "").lower() for k in ext.api_errors
        }:
            err_msg = errors_lower.get(platform, "")
            if not err_msg:
                # Try fuzzy key match (e.g. "Last.fm" vs "lastfm")
                for k, v in ext.api_errors.items():
                    if k.lower().replace(".", "").replace(" ", "") == platform.replace(".", ""):
                        err_msg = v
                        break
            if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                status[platform] = "timeout"
            else:
                status[platform] = "error"
        else:
            status[platform] = "not_found"
    return status


def _build_profile_urls(ext: ExternalData) -> dict:
    """Build profile URLs per platform from available data."""
    urls: dict = {}
    if ext.wikipedia_url:
        urls["wikipedia"] = ext.wikipedia_url
    if ext.musicbrainz_bandcamp_url:
        urls["bandcamp"] = ext.musicbrainz_bandcamp_url
    if ext.musicbrainz_official_website:
        urls["official_website"] = ext.musicbrainz_official_website
    if ext.musicbrainz_youtube_url:
        urls["youtube"] = ext.musicbrainz_youtube_url
    # Social URLs from MusicBrainz
    social: dict = {}
    for rel_type, url in ext.musicbrainz_urls.items():
        url_lower = url.lower()
        if "facebook" in url_lower:
            social["facebook"] = url
        elif "instagram" in url_lower:
            social["instagram"] = url
        elif "twitter" in url_lower or "x.com" in url_lower:
            social["twitter"] = url
    # Add from Genius
    if ext.genius_facebook_name:
        social.setdefault("facebook", f"https://facebook.com/{ext.genius_facebook_name}")
    if ext.genius_instagram_name:
        social.setdefault("instagram", f"https://instagram.com/{ext.genius_instagram_name}")
    if ext.genius_twitter_name:
        social.setdefault("twitter", f"https://twitter.com/{ext.genius_twitter_name}")
    if social:
        urls["social"] = social
    return urls


def _build_bio_data(ext: ExternalData) -> dict:
    """Build bio analysis data for frontend display."""
    sources: list[str] = []
    total_chars = 0
    if ext.wikipedia_found and ext.wikipedia_length > 0:
        sources.append("wikipedia")
        # Wikipedia length is in bytes; approximate chars
        total_chars += ext.wikipedia_length
    if ext.discogs_profile and len(ext.discogs_profile) > 0:
        sources.append("discogs")
        total_chars += len(ext.discogs_profile)
    if ext.genius_found and ext.genius_description:
        sources.append("genius")
        total_chars += len(ext.genius_description)
    if ext.lastfm_bio_exists:
        sources.append("lastfm")

    # Check for generic/boilerplate bio
    is_generic = total_chars < 200 and len(sources) <= 1

    return {
        "sources": sources,
        "total_chars": total_chars,
        "has_verifiable_details": total_chars >= 200 and len(sources) >= 2,
        "real_name": ext.discogs_realname or "",
        "is_generic": is_generic,
    }


def _external_data_to_dict(ext: ExternalData) -> dict:
    """Serialize all ExternalData fields."""
    d: dict = {}
    # Genius
    if ext.genius_found:
        d["genius"] = {
            "found": True, "songs": ext.genius_song_count,
            "verified": ext.genius_is_verified, "followers": ext.genius_followers_count,
            "facebook": ext.genius_facebook_name, "instagram": ext.genius_instagram_name,
            "twitter": ext.genius_twitter_name, "alternate_names": ext.genius_alternate_names,
        }
    # Discogs
    if ext.discogs_found:
        d["discogs"] = {
            "found": True, "physical": ext.discogs_physical_releases,
            "digital": ext.discogs_digital_releases, "total": ext.discogs_total_releases,
            "formats": ext.discogs_formats, "labels": ext.discogs_labels,
            "bio_length": len(ext.discogs_profile), "realname": ext.discogs_realname,
            "members": ext.discogs_members, "groups": ext.discogs_groups,
        }
    # Setlist.fm
    if ext.setlistfm_found:
        d["setlistfm"] = {
            "found": True, "shows": ext.setlistfm_total_shows,
            "first_show": ext.setlistfm_first_show, "last_show": ext.setlistfm_last_show,
            "countries": ext.setlistfm_venue_countries, "tours": ext.setlistfm_tour_names,
        }
    # MusicBrainz
    if ext.musicbrainz_found:
        d["musicbrainz"] = {
            "found": True, "type": ext.musicbrainz_type,
            "country": ext.musicbrainz_country, "begin_date": ext.musicbrainz_begin_date,
            "genres": ext.musicbrainz_genres, "isnis": ext.musicbrainz_isnis,
            "ipis": ext.musicbrainz_ipis, "gender": ext.musicbrainz_gender,
        }
    # Last.fm
    if ext.lastfm_found:
        d["lastfm"] = {
            "found": True, "listeners": ext.lastfm_listeners,
            "playcount": ext.lastfm_playcount, "ratio": ext.lastfm_listener_play_ratio,
            "tags": ext.lastfm_tags, "similar": ext.lastfm_similar_artists,
            "bio_exists": ext.lastfm_bio_exists,
        }
    # Wikipedia
    if ext.wikipedia_found:
        d["wikipedia"] = {
            "found": True, "title": ext.wikipedia_title,
            "length": ext.wikipedia_length, "monthly_views": ext.wikipedia_monthly_views,
            "categories": ext.wikipedia_categories, "url": ext.wikipedia_url,
        }
    # Songkick
    if ext.songkick_found:
        d["songkick"] = {
            "found": True, "on_tour": ext.songkick_on_tour,
            "past_events": ext.songkick_total_past_events,
            "upcoming": ext.songkick_total_upcoming_events,
            "first_event": ext.songkick_first_event_date,
            "last_event": ext.songkick_last_event_date,
            "countries": ext.songkick_venue_countries,
        }
    # YouTube
    if ext.youtube_checked and ext.youtube_channel_found:
        d["youtube"] = {
            "found": True, "subscribers": ext.youtube_subscriber_count,
            "videos": ext.youtube_video_count, "views": ext.youtube_view_count,
            "music_videos": ext.youtube_music_videos_found,
        }
    # PRO Registry
    if ext.pro_checked:
        pro_dict: dict = {
            "checked": True, "bmi": ext.pro_found_bmi, "ascap": ext.pro_found_ascap,
            "works": ext.pro_works_count, "publishers": ext.pro_publishers,
            "songwriter_registered": ext.pro_songwriter_registered,
            "pfc_publisher_match": ext.pro_pfc_publisher_match,
        }
        if ext.pro_songwriter_share_pct >= 0:
            pro_dict["songwriter_share_pct"] = ext.pro_songwriter_share_pct
        if ext.pro_publisher_share_pct >= 0:
            pro_dict["publisher_share_pct"] = ext.pro_publisher_share_pct
        if ext.pro_zero_songwriter_share:
            pro_dict["zero_songwriter_share"] = True
        d["pro_registry"] = pro_dict
    # Press
    if ext.press_checked:
        d["press"] = {
            "checked": True, "publications": ext.press_publications_found,
            "total_hits": ext.press_total_hits,
        }
    # ISRC
    if ext.isrcs:
        d["isrc"] = {"codes": ext.isrcs[:5], "registrants": ext.isrc_registrants}
    # Deezer AI
    if ext.deezer_ai_checked:
        d["deezer_ai"] = {"checked": True, "tagged_albums": ext.deezer_ai_tagged_albums}
    # Match quality
    if ext.match_confidences:
        d["match_quality"] = {
            "confidences": ext.match_confidences,
            "methods": ext.match_methods,
            "had_platform_ids": ext.had_platform_ids,
        }
    return d


def _evidence_to_dict(e: Evidence) -> dict:
    d = {
        "finding": e.finding,
        "source": e.source,
        "type": e.evidence_type,
        "strength": e.strength,
        "detail": e.detail,
    }
    if e.tags:
        d["tags"] = list(e.tags)
    return d


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def to_markdown(report: PlaylistReport) -> str:
    lines: list[str] = []

    lines.append(f"# Playlist Audit Report: {report.playlist_name}")
    lines.append("")
    lines.append(f"**Playlist ID:** `{report.playlist_id}`")
    lines.append(f"**Owner:** {report.owner}")
    lines.append(f"**Total tracks:** {report.total_tracks}")
    lines.append(f"**Unique artists analyzed:** {report.total_unique_artists}")
    if report.is_spotify_owned:
        lines.append("**Spotify-owned playlist:** Yes")
    lines.append("")

    # Health score
    lines.append(f"## Playlist Health Score: {report.health_score}/100")
    lines.append("")

    # Verdict breakdown
    lines.append("## Verdict Breakdown")
    lines.append("")
    lines.append("| Verdict | Count |")
    lines.append("|---|---|")
    lines.append(f"| Verified Artist | {report.verified_artists} |")
    lines.append(f"| Likely Authentic | {report.likely_authentic} |")
    lines.append(f"| Inconclusive | {report.inconclusive} |")
    lines.append(f"| Suspicious | {report.suspicious} |")
    lines.append(f"| Likely Artificial | {report.likely_artificial} |")
    lines.append("")

    # Artist evaluation table
    lines.append("## Artist Evaluations")
    lines.append("")
    lines.append("| Verdict | Artist | Threat Type | APIs Reached | Key Evidence | Confidence |")
    lines.append("|---|---|---|---|---|---|")
    for a in report.artists:
        ev = a.evaluation
        threat = a.threat_category_name or "-"
        if ev:
            key_ev = _md_key_evidence(ev)
            sources = ev.sources_reached
            api_str = ", ".join(n for n, r in sources.items() if r)
            lines.append(f"| {ev.verdict.value} | {a.artist_name} | {threat} | {api_str} | {key_ev} | {ev.confidence} |")
        else:
            lines.append(f"| {a.label} | {a.artist_name} | {threat} | - | - | - |")
    lines.append("")

    # Detailed evidence cards for all artists
    has_evidence = [a for a in report.artists if a.evaluation]
    if has_evidence:
        lines.append("## Evidence Details")
        lines.append("")
        for a in has_evidence:
            ev = a.evaluation
            if not ev:
                continue

            lines.append(f"### {a.artist_name}")
            lines.append("")
            lines.append(f"**Verdict:** {ev.verdict.value} ({ev.confidence} confidence)")
            if a.threat_category_name:
                lines.append(f"**Threat Category:** {a.threat_category_name}")
            lines.append("")

            # Category scores
            scores = ev.category_scores
            lines.append("**Signal Scores:**")
            lines.append("")
            for cat, score in scores.items():
                bar = "\u2588" * (score // 5) + "\u2591" * (20 - score // 5)
                lines.append(f"- {cat}: {bar} {score}/100")
            lines.append("")

            # API sources
            sources = ev.sources_reached
            lines.append("**Data Sources:**")
            lines.append("")
            for name, reached in sources.items():
                status = "found" if reached else "not found"
                lines.append(f"- {name}: {status}")
            lines.append("")

            # Platform presence
            platforms = ev.platform_presence.names()
            if platforms:
                lines.append(f"**Found on:** {', '.join(platforms)}")
            lines.append("")

            # Decision path
            if ev.decision_path:
                lines.append(f"**How we decided:** {' -> '.join(ev.decision_path)}")
                lines.append("")

            # Red flags
            if ev.red_flags:
                lines.append("**Red Flags:**")
                lines.append("")
                for e in ev.red_flags:
                    lines.append(f"- [{e.strength.upper()}] {e.finding} ({e.source})")
                    lines.append(f"  - {e.detail}")
                lines.append("")

            # Green flags
            if ev.green_flags:
                lines.append("**Green Flags:**")
                lines.append("")
                for e in ev.green_flags:
                    lines.append(f"- [{e.strength.upper()}] {e.finding} ({e.source})")
                    lines.append(f"  - {e.detail}")
                lines.append("")

            # Notes
            if ev.neutral_notes:
                lines.append("**Notes:**")
                lines.append("")
                for e in ev.neutral_notes:
                    lines.append(f"- {e.finding} ({e.source})")
                lines.append("")

            lines.append("---")
            lines.append("")

    lines.append(f"*Report generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    return "\n".join(lines)


def _md_key_evidence(ev: ArtistEvaluation) -> str:
    """Short summary for the markdown table."""
    parts: list[str] = []
    platforms = ev.platform_presence.count()
    if platforms >= 2:
        parts.append(f"{platforms} platforms")
    if ev.platform_presence.deezer_fans:
        parts.append(f"{ev.platform_presence.deezer_fans:,} fans")
    if ev.red_flags:
        parts.append(f"{len(ev.red_flags)} red flags")
    if ev.green_flags:
        parts.append(f"{len(ev.green_flags)} green flags")
    return ", ".join(parts) if parts else "-"


# ---------------------------------------------------------------------------
# HTML — Full report with summary dashboard + expandable artist detail cards
# ---------------------------------------------------------------------------

_VERDICT_COLORS = {
    "Verified Artist": "#22c55e",
    "Likely Authentic": "#86efac",
    "Inconclusive": "#fbbf24",
    "Insufficient Data": "#fbbf24",
    "Conflicting Signals": "#fbbf24",
    "Suspicious": "#f97316",
    "Likely Artificial": "#ef4444",
    "Not Scanned": "#9ca3af",
}

_THREAT_COLORS = {
    "PFC Ghost Artist": "#f59e0b",
    "PFC + AI Hybrid": "#f97316",
    "Independent AI Artist": "#a78bfa",
    "AI Fraud Farm": "#ef4444",
    "AI Impersonation": "#ec4899",
}

_VERDICT_SORT = {
    "Likely Artificial": 0, "Suspicious": 1, "Inconclusive": 2,
    "Insufficient Data": 2, "Conflicting Signals": 2,
    "Likely Authentic": 3, "Verified Artist": 4,
}

_STRENGTH_PTS = {"strong": 3, "moderate": 2, "weak": 1}


def _esc(text: str) -> str:
    """HTML-escape text."""
    return html_mod.escape(str(text))


def _fmt_num(n: int) -> str:
    """Format a number with commas."""
    return f"{n:,}"


def _strength_dots(strength: str, color_on: str = "#ef4444") -> str:
    """Render strength indicator dots."""
    pts = _STRENGTH_PTS.get(strength, 1)
    on = f'<span style="color:{color_on}">&#9679;</span>'
    off = '<span style="color:#333">&#9679;</span>'
    label = strength.capitalize()
    return f'{on * pts}{off * (3 - pts)} <span style="color:#666;font-size:0.75rem">{label}</span>'


# ---------------------------------------------------------------------------
# SVG Radar Chart (inline, no JS library needed)
# ---------------------------------------------------------------------------

def _radar_svg(scores: dict[str, int], color: str, size: int = 260) -> str:
    """Generate an inline SVG hexagonal radar chart."""
    labels = list(scores.keys())
    values = [scores[k] / 100.0 for k in labels]  # normalize to 0-1
    n = len(labels)
    if n < 3:
        return ""

    cx, cy = size / 2, size / 2
    r = size / 2 - 30  # leave room for labels

    def _point(angle_idx: int, radius_frac: float) -> tuple[float, float]:
        angle = (2 * math.pi * angle_idx / n) - math.pi / 2
        return cx + radius_frac * r * math.cos(angle), cy + radius_frac * r * math.sin(angle)

    # Grid lines at 25%, 50%, 75%, 100%
    grid_lines = ""
    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{_point(i, frac)[0]:.1f},{_point(i, frac)[1]:.1f}" for i in range(n))
        grid_lines += f'<polygon points="{pts}" fill="none" stroke="#1a2332" stroke-width="1"/>\n'

    # Spoke lines
    spokes = ""
    for i in range(n):
        x, y = _point(i, 1.0)
        spokes += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#1a2332" stroke-width="1"/>\n'

    # Data polygon
    data_pts = " ".join(f"{_point(i, max(v, 0.02))[0]:.1f},{_point(i, max(v, 0.02))[1]:.1f}"
                        for i, v in enumerate(values))

    # Labels
    label_elems = ""
    for i, label in enumerate(labels):
        lx, ly = _point(i, 1.22)
        anchor = "middle"
        if lx < cx - 10:
            anchor = "end"
        elif lx > cx + 10:
            anchor = "start"
        score_val = scores[label]
        label_elems += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#8899aa" font-size="9" '
            f'text-anchor="{anchor}" dominant-baseline="middle">'
            f'{_esc(label)}</text>\n'
            f'<text x="{lx:.1f}" y="{ly + 12:.1f}" fill="{color}" font-size="10" '
            f'font-weight="bold" text-anchor="{anchor}" dominant-baseline="middle">'
            f'{score_val}</text>\n'
        )

    return f"""<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}"
     xmlns="http://www.w3.org/2000/svg" style="display:block">
  {grid_lines}
  {spokes}
  <polygon points="{data_pts}" fill="{color}" fill-opacity="0.15"
           stroke="{color}" stroke-width="2"/>
  {label_elems}
</svg>"""


# ---------------------------------------------------------------------------
# Health score gauge (SVG semicircle)
# ---------------------------------------------------------------------------

def _health_gauge_svg(score: int) -> str:
    """Generate an SVG semicircular gauge for the health score."""
    if score >= 75:
        color = "#22c55e"
    elif score >= 40:
        color = "#f59e0b"
    else:
        color = "#ef4444"

    # Semicircle arc from 180 to 0 degrees
    r = 70
    cx, cy = 80, 85
    circumference = math.pi * r
    filled = circumference * score / 100

    return f"""<svg viewBox="0 0 160 100" width="200" height="125" xmlns="http://www.w3.org/2000/svg">
  <path d="M 10 85 A 70 70 0 0 1 150 85" fill="none" stroke="#1a2332" stroke-width="12" stroke-linecap="round"/>
  <path d="M 10 85 A 70 70 0 0 1 150 85" fill="none" stroke="{color}" stroke-width="12"
        stroke-linecap="round" stroke-dasharray="{filled:.1f} {circumference:.1f}"/>
  <text x="{cx}" y="{cy - 10}" fill="{color}" font-size="36" font-weight="bold"
        text-anchor="middle" dominant-baseline="middle">{score}</text>
  <text x="{cx}" y="{cy + 12}" fill="#667" font-size="10"
        text-anchor="middle">Health Score</text>
</svg>"""


# ---------------------------------------------------------------------------
# Stacked bar helpers
# ---------------------------------------------------------------------------

def _stacked_bar(segments: list[tuple[str, int, str]], total: int) -> str:
    """Render a horizontal stacked bar."""
    if total == 0:
        return '<div style="height:28px;background:#1a2332;border-radius:4px"></div>'
    parts = []
    for label, count, color in segments:
        if count <= 0:
            continue
        pct = count / total * 100
        parts.append(
            f'<div style="width:{pct:.1f}%;background:{color};display:flex;align-items:center;'
            f'justify-content:center;font-size:0.7rem;color:#fff;white-space:nowrap;'
            f'min-width:20px" title="{_esc(label)}: {count}">{count}</div>'
        )
    return (
        '<div style="display:flex;height:28px;border-radius:4px;overflow:hidden;gap:1px">'
        + "".join(parts) + '</div>'
    )


# ---------------------------------------------------------------------------
# Main HTML generator
# ---------------------------------------------------------------------------

def to_html(report: PlaylistReport) -> str:
    """Generate a self-contained HTML report matching the output spec."""
    now = datetime.now(timezone.utc)

    # Sort artists: worst verdict first, then by score ascending
    sorted_artists = sorted(
        report.artists,
        key=lambda a: (_VERDICT_SORT.get(a.verdict, 2), a.final_score),
    )

    # Compute threat category breakdown
    threat_counts: dict[str, int] = {}
    for a in sorted_artists:
        if a.threat_category_name:
            threat_counts[a.threat_category_name] = threat_counts.get(a.threat_category_name, 0) + 1

    # Compute metrics
    flagged = report.suspicious + report.likely_artificial
    skipped_count = len(report.skipped_artists) if report.skipped_artists else 0
    total_bar = report.total_unique_artists + skipped_count

    # Derive scan tier from artist data
    has_deep = any("deep" in a.tiers_completed for a in report.artists)
    scan_tier = "Deep Dive" if has_deep else "Full Analysis"

    # Build artist cards
    artist_cards_html = []
    for idx, a in enumerate(sorted_artists):
        ev = a.evaluation
        artist_cards_html.append(_build_card(a, ev, idx))

    # Verdict bar segments (spec Part 1: updated colors + Not Scanned segment)
    verdict_segments = [
        ("Verified Artist", report.verified_artists, "#22c55e"),
        ("Likely Authentic", report.likely_authentic, "#86efac"),
        ("Inconclusive", report.inconclusive, "#fbbf24"),
        ("Suspicious", report.suspicious, "#f97316"),
        ("Likely Artificial", report.likely_artificial, "#ef4444"),
    ]
    if skipped_count > 0:
        verdict_segments.append(("Not Scanned", skipped_count, "#9ca3af"))

    # Threat bar segments
    threat_segments = [
        (name, threat_counts.get(name, 0), _THREAT_COLORS.get(name, "#888"))
        for name in ["PFC Ghost Artist", "PFC + AI Hybrid", "Independent AI Artist",
                      "AI Fraud Farm", "AI Impersonation"]
    ]

    # Duration (kept for footer only)
    duration_str = ""
    if report.scan_duration_seconds:
        mins = int(report.scan_duration_seconds // 60)
        secs = int(report.scan_duration_seconds % 60)
        duration_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Playlist Audit: {_esc(report.playlist_name)}</title>
<style>
:root {{
  --bg: #06090f;
  --card: #0d1219;
  --border: #1a2332;
  --accent: #1DB954;
  --text: #c8d0da;
  --text-dim: #667788;
  --text-bright: #e8eef4;
}}
*,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0 }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px }}

/* Header */
.header {{
  text-align: center;
  margin-bottom: 32px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}}
.header h1 {{
  font-size: 1.5rem;
  color: var(--text-bright);
  margin-bottom: 4px;
}}
.header .subtitle {{
  color: var(--text-dim);
  font-size: 0.9rem;
}}

/* Summary section */
.summary {{
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 24px;
  margin-bottom: 32px;
  padding: 24px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}}
@media (max-width: 768px) {{
  .summary {{ grid-template-columns: 1fr; }}
}}
.gauge-col {{ display: flex; flex-direction: column; align-items: center; }}
.gauge-subtitle {{
  color: var(--text-dim);
  font-size: 0.8rem;
  text-align: center;
  max-width: 200px;
}}
.metrics-col {{ display: flex; flex-direction: column; gap: 16px; }}

/* Metric cards */
.metric-row {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 12px;
}}
.metric-card {{
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  text-align: center;
}}
.metric-value {{
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--text-bright);
  font-variant-numeric: tabular-nums;
}}
.metric-label {{
  font-size: 0.72rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}}

/* Verdict / threat bars */
.bar-section {{ margin-bottom: 12px; }}
.bar-label {{
  font-size: 0.75rem;
  color: var(--text-dim);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}}
.legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 6px;
  font-size: 0.72rem;
  color: var(--text-dim);
}}
.legend-dot {{
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  margin-right: 3px;
  vertical-align: middle;
}}

/* Sources */
.sources-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 0.8rem;
  color: var(--text-dim);
}}
.src-dot {{ display: inline-flex; align-items: center; gap: 4px; }}
.dot-ok {{
  display: inline-block;
  width: 6px; height: 6px;
  border-radius: 50%;
  background: #22c55e;
}}

/* Artist list controls */
.list-controls {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}}
.list-controls h2 {{ font-size: 1.1rem; color: var(--text-bright); }}
.toggle-btn {{
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--text-dim);
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 0.78rem;
  cursor: pointer;
}}
.toggle-btn:hover {{ border-color: var(--accent); color: var(--accent); }}

/* Artist cards */
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  margin-bottom: 8px;
  overflow: hidden;
  transition: border-color 0.15s;
}}
.card:hover {{ border-color: #2a3a4a; }}
.card-row {{
  display: flex;
  align-items: flex-start;
  padding: 12px 16px;
  gap: 12px;
  cursor: pointer;
  user-select: none;
  flex-wrap: wrap;
}}
.score-badge {{
  width: 38px; height: 38px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 0.85rem;
  font-weight: 700;
  flex-shrink: 0;
  color: #fff;
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
}}
.card-info {{ flex: 1; min-width: 0; }}
.card-name {{
  font-weight: 600;
  color: var(--text-bright);
  word-break: break-word;
}}
.card-stats {{
  font-size: 0.78rem;
  color: var(--text-dim);
  line-height: 1.6;
}}
.pill {{
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 0.72rem;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
  margin-top: 2px;
}}
.threat-pill {{
  font-size: 0.68rem;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
  flex-shrink: 0;
  margin-top: 2px;
}}
.chevron {{
  color: var(--text-dim);
  font-size: 0.8rem;
  transition: transform 0.2s;
  flex-shrink: 0;
  margin-top: 6px;
}}
.card.open .chevron {{ transform: rotate(180deg); }}

/* Card body */
.card-body {{
  display: none;
  padding: 0 16px 16px;
  border-top: 1px solid var(--border);
}}
.card.open .card-body {{ display: block; }}

/* Explanation */
.explanation {{
  padding: 12px;
  margin: 12px 0;
  border-radius: 6px;
  font-size: 0.88rem;
  line-height: 1.55;
}}

/* Scorecard grid */
.scorecard {{
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 20px;
  margin: 12px 0;
}}
@media (max-width: 768px) {{
  .scorecard {{ grid-template-columns: 1fr; }}
}}

/* Metadata grid */
.meta-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 6px 16px;
  font-size: 0.82rem;
  margin: 12px 0;
}}
.meta-item {{ display: flex; gap: 6px; }}
.meta-key {{ color: var(--text-dim); white-space: nowrap; }}
.meta-val {{ color: var(--text); font-weight: 500; }}

/* Evidence section */
.evidence-section {{
  margin: 16px 0;
}}
.evidence-cols {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}}
@media (max-width: 768px) {{
  .evidence-cols {{ grid-template-columns: 1fr; }}
}}
.evidence-col-header {{
  font-size: 0.82rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border);
}}
.flag-item {{
  padding: 8px 0;
  border-bottom: 1px solid #111820;
  font-size: 0.82rem;
}}
.flag-finding {{
  font-weight: 600;
  color: var(--text-bright);
  margin-bottom: 2px;
}}
.flag-detail {{
  color: var(--text-dim);
  font-size: 0.78rem;
  line-height: 1.4;
}}
.flag-meta {{
  font-size: 0.7rem;
  color: #445;
  margin-top: 2px;
}}
.pts-summary {{
  font-size: 0.78rem;
  color: var(--text-dim);
  padding: 8px 0;
  margin-top: 4px;
  border-top: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}}

/* Related entities */
.entities {{
  margin: 12px 0;
  padding: 12px;
  background: #0a0e14;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 0.82rem;
}}
.entity-item {{
  padding: 4px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}}

/* Signal bars */
.signal-bars {{ margin: 8px 0; }}
.signal-row {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 0.78rem;
}}
.signal-label {{ width: 120px; color: var(--text-dim); text-align: right; flex-shrink: 0; }}
.signal-track {{
  flex: 1;
  height: 8px;
  background: #111820;
  border-radius: 4px;
  overflow: hidden;
}}
.signal-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
.signal-val {{ width: 28px; color: var(--text-dim); font-variant-numeric: tabular-nums; }}

/* Axis bucket grid (expanded card body) */
.axis-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin: 12px 0;
}}
@media (max-width: 768px) {{
  .axis-grid {{ grid-template-columns: 1fr; }}
}}
.axis-bucket {{
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}}
.axis-header {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}}
.axis-name {{
  font-weight: 600;
  font-size: 0.8rem;
  color: var(--text-bright);
  flex: 1;
}}
.axis-score {{
  font-size: 0.8rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}}
.axis-bar {{
  height: 4px;
  background: #111820;
  border-radius: 2px;
  margin-bottom: 8px;
  overflow: hidden;
}}
.axis-bar-fill {{ height: 100%; border-radius: 2px; }}
.axis-item {{
  font-size: 0.78rem;
  color: var(--text-dim);
  padding: 2px 0;
  display: flex;
  gap: 6px;
  align-items: flex-start;
  line-height: 1.4;
}}
.axis-icon {{
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  font-size: 0.72rem;
}}

/* Methodology section */
.methodology {{
  margin-bottom: 32px;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}}
.methodology summary {{
  padding: 14px 20px;
  cursor: pointer;
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--text-dim);
  background: var(--card);
  list-style: none;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.methodology summary::-webkit-details-marker {{ display: none; }}
.methodology summary::before {{
  content: '\\25B6';
  font-size: 0.6rem;
  transition: transform 0.2s;
}}
.methodology[open] summary::before {{ transform: rotate(90deg); }}
.methodology-body {{
  padding: 20px;
  background: var(--card);
  border-top: 1px solid var(--border);
  font-size: 0.82rem;
  color: var(--text);
  line-height: 1.65;
}}
.methodology-body h3 {{
  font-size: 0.82rem;
  color: var(--text-bright);
  margin: 16px 0 6px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}}
.methodology-body h3:first-child {{ margin-top: 0; }}
.q-grid {{
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  margin: 8px 0;
}}
.q-item {{
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
}}
.q-num {{
  color: var(--accent);
  font-weight: 700;
  font-size: 0.85rem;
  flex-shrink: 0;
  width: 18px;
}}
.q-text {{ color: var(--text-bright); font-weight: 600; }}
.q-detail {{ color: var(--text-dim); font-size: 0.78rem; margin-top: 2px; }}
.verdict-table {{
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 0.78rem;
}}
.verdict-table th {{
  text-align: left;
  padding: 6px 10px;
  color: var(--text-dim);
  border-bottom: 1px solid var(--border);
  font-weight: 600;
}}
.verdict-table td {{
  padding: 6px 10px;
  border-bottom: 1px solid #111820;
}}
.verdict-table td:first-child {{ font-weight: 600; }}

/* Footer */
.footer {{
  margin-top: 40px;
  padding: 20px 0;
  border-top: 1px solid var(--border);
  text-align: center;
  font-size: 0.75rem;
  color: var(--text-dim);
  line-height: 1.7;
}}
</style>
</head>
<body>
<div class="container">

<!-- Header -->
<div class="header">
  <h1>Playlist Authenticity Report</h1>
  <div class="subtitle">{_esc(report.playlist_name)} &middot; by {_esc(report.owner)} &middot; {scan_tier} &middot; {now.strftime('%Y-%m-%d')}</div>
</div>

<!-- Summary -->
<div class="summary">
  <div class="gauge-col">
    {_health_gauge_svg(report.health_score)}
    <div class="gauge-subtitle">
      {report.health_score}% of artists show legitimacy signals
    </div>
  </div>
  <div class="metrics-col">
    <!-- Key metrics (spec Part 1: removed Contamination, Scan Time, API Calls) -->
    <div class="metric-row">
      <div class="metric-card">
        <div class="metric-value">{report.total_tracks}</div>
        <div class="metric-label">Tracks</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{report.total_unique_artists}</div>
        <div class="metric-label">Analyzed</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{flagged}</div>
        <div class="metric-label">Flagged</div>
      </div>
      {f'<div class="metric-card"><div class="metric-value" style="color:#f97316">{skipped_count} &#9888;</div><div class="metric-label">Timed Out</div></div>' if skipped_count else ''}
    </div>

    <!-- Verdict bar (spec Part 1: updated colors + Not Scanned segment) -->
    <div class="bar-section">
      <div class="bar-label">Verdict Breakdown</div>
      {_stacked_bar(verdict_segments, total_bar)}
      <div class="legend">
        <span><span class="legend-dot" style="background:#22c55e"></span>Verified</span>
        <span><span class="legend-dot" style="background:#86efac"></span>Authentic</span>
        <span><span class="legend-dot" style="background:#fbbf24"></span>Inconclusive</span>
        <span><span class="legend-dot" style="background:#f97316"></span>Suspicious</span>
        <span><span class="legend-dot" style="background:#ef4444"></span>Artificial</span>
        {f'<span><span class="legend-dot" style="background:#9ca3af"></span>Not Scanned</span>' if skipped_count else ''}
      </div>
    </div>

    <!-- Threat bar (nested under flagged, only if flagged artists) -->
    {_threat_bar_section(threat_segments, flagged) if flagged else ''}
  </div>
</div>

<!-- How This Works -->
<details class="methodology">
  <summary>How This Works &mdash; Six Evidence Categories</summary>
  <div class="methodology-body">
    <p>For every artist on this playlist, we queried up to 9 independent platforms (plus PRO registries and
    Deezer AI detection) and checked against curated databases of known bad actors. The analysis evaluates six dimensions:</p>

    <div class="q-grid">
      <div class="q-item">
        <span class="q-num">1</span>
        <div>
          <div class="q-text">Platform Presence</div>
          <div class="q-detail">Where does this artist exist? Deezer, MusicBrainz, Genius, Last.fm, Discogs, Setlist.fm, Songkick, YouTube, Wikipedia, social media. Ghost artists leave zero traces outside Spotify.</div>
        </div>
      </div>
      <div class="q-item">
        <span class="q-num">2</span>
        <div>
          <div class="q-text">Fan Engagement</div>
          <div class="q-detail">Do real humans listen? Last.fm scrobbles and play/listener ratio, Deezer fans, Genius followers. Key metric: repeat listeners.</div>
        </div>
      </div>
      <div class="q-item">
        <span class="q-num">3</span>
        <div>
          <div class="q-text">Creative History</div>
          <div class="q-detail">Release cadence by year, track durations, album-to-single ratio, collaborative songwriting, title patterns. Content farms release 40+ short singles with no albums.</div>
        </div>
      </div>
      <div class="q-item">
        <span class="q-num">4</span>
        <div>
          <div class="q-text">IRL Presence</div>
          <div class="q-detail">Live concert history on Setlist.fm and Songkick. Physical releases on Discogs. Touring geography. Named tours. Upcoming shows.</div>
        </div>
      </div>
      <div class="q-item">
        <span class="q-num">5</span>
        <div>
          <div class="q-text">Industry Signals</div>
          <div class="q-detail">MusicBrainz metadata (type, country, dates). ISNI/IPI codes. ASCAP/BMI songwriter registration with ownership splits. Genius profile depth. Discogs bio{', and Claude AI synthesis' if has_deep else ''}.</div>
        </div>
      </div>
      <div class="q-item">
        <span class="q-num">6</span>
        <div>
          <div class="q-text">Blocklist Status</div>
          <div class="q-detail">Artist name, label, distributor, and credited songwriters checked against investigated databases of confirmed PFC providers, known AI artists, and PFC publishers.</div>
        </div>
      </div>
    </div>

    <h3>Verdicts</h3>
    <table class="verdict-table">
      <tr><th>Verdict</th><th>Score</th><th>Meaning</th></tr>
      <tr><td style="color:#22c55e">Verified Artist</td><td>82&ndash;100</td><td>Strong evidence of legitimacy across multiple independent sources.</td></tr>
      <tr><td style="color:#86efac">Likely Authentic</td><td>58&ndash;81</td><td>More green flags than red. Probably a real artist.</td></tr>
      <tr><td style="color:#fbbf24">Inconclusive</td><td>38&ndash;57</td><td>Insufficient data or conflicting signals.</td></tr>
      <tr><td style="color:#f97316">Suspicious</td><td>18&ndash;37</td><td>More red flags than green. Probably fraudulent.</td></tr>
      <tr><td style="color:#ef4444">Likely Artificial</td><td>0&ndash;17</td><td>Known bad actor match, or overwhelming evidence of fraud.</td></tr>
    </table>

    <h3>Threat Categories</h3>
    <table class="verdict-table">
      <tr><th>Category</th><th>Description</th></tr>
      <tr><td style="color:#f59e0b">PFC Ghost Artist</td><td>Human-made music commissioned by Spotify at a flat fee, published under fake names.</td></tr>
      <tr><td style="color:#f97316">PFC + AI Hybrid</td><td>Same PFC infrastructure, but using AI tools instead of human session musicians.</td></tr>
      <tr><td style="color:#a78bfa">Independent AI</td><td>AI-generated music uploaded by individuals, not affiliated with PFC pipeline.</td></tr>
      <tr><td style="color:#ef4444">AI Fraud Farm</td><td>Mass-produced AI content uploaded with bots for streaming revenue fraud.</td></tr>
      <tr><td style="color:#ec4899">AI Impersonation</td><td>AI-generated tracks uploaded to a real artist's page without consent.</td></tr>
    </table>
  </div>
</details>

<!-- Artist list -->
<div class="list-controls">
  <h2>Artist Analysis ({len(sorted_artists)})</h2>
  <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
    <span style="font-size:0.72rem;color:var(--text-dim)">Sort:</span>
    <button class="toggle-btn" onclick="sortCards('score-asc')" id="sort-score-asc">Score &#8593;</button>
    <button class="toggle-btn" onclick="sortCards('score-desc')" id="sort-score-desc">Score &#8595;</button>
    <button class="toggle-btn" onclick="sortCards('alpha')" id="sort-alpha">A-Z</button>
    <span style="margin-left:8px"></span>
    <button class="toggle-btn" onclick="filterCards('all')" id="filter-all" style="border-color:var(--accent)">All</button>
    <button class="toggle-btn" onclick="filterCards('flagged')" id="filter-flagged">Flagged</button>
    <span style="margin-left:8px"></span>
    <button class="toggle-btn" onclick="toggleAll()">Expand All</button>
  </div>
</div>

{"".join(artist_cards_html)}

{_build_skipped_section(report.skipped_artists)}

<!-- Footer -->
<div class="footer">
  Generated by Playlist Authenticity Analyzer &middot; {scan_tier} &middot; {now.strftime('%Y-%m-%d %H:%M UTC')}<br>
  {len(sorted_artists)} artists analyzed across 6 evidence categories
  {f' &middot; {duration_str}' if duration_str else ''}<br>
  {f'{_esc(report.blocklist_version)}' if report.blocklist_version else ''}
</div>

</div>
<script>
function toggleCard(el) {{
  el.closest('.card').classList.toggle('open');
}}
function toggleAll() {{
  const cards = document.querySelectorAll('.card[data-idx]');
  const anyOpen = document.querySelector('.card.open');
  cards.forEach(c => {{
    if (anyOpen) c.classList.remove('open');
    else c.classList.add('open');
  }});
}}
function sortCards(mode) {{
  const container = document.querySelector('.card[data-idx]')?.parentElement;
  if (!container) return;
  const cards = Array.from(container.querySelectorAll('.card[data-idx]'));
  cards.sort((a, b) => {{
    if (mode === 'score-asc') return (parseInt(a.dataset.score) || 0) - (parseInt(b.dataset.score) || 0);
    if (mode === 'score-desc') return (parseInt(b.dataset.score) || 0) - (parseInt(a.dataset.score) || 0);
    if (mode === 'alpha') return (a.dataset.name || '').localeCompare(b.dataset.name || '');
    return 0;
  }});
  cards.forEach(c => container.appendChild(c));
  // Update active button
  document.querySelectorAll('[id^="sort-"]').forEach(b => b.style.borderColor = '');
  var el = document.getElementById('sort-' + mode);
  if (el) el.style.borderColor = 'var(--accent)';
}}
function filterCards(mode) {{
  const cards = document.querySelectorAll('.card[data-idx]');
  cards.forEach(c => {{
    if (mode === 'all') c.style.display = '';
    else if (mode === 'flagged') c.style.display = c.dataset.flagged === 'true' ? '' : 'none';
  }});
  document.querySelectorAll('[id^="filter-"]').forEach(b => b.style.borderColor = '');
  var el = document.getElementById('filter-' + mode);
  if (el) el.style.borderColor = 'var(--accent)';
}}
</script>
</body>
</html>"""
    return page


def _threat_bar_section(segments: list[tuple[str, int, str]], total: int) -> str:
    """Render threat category bar section."""
    bar = _stacked_bar(segments, total)
    legend_items = []
    for name, count, color in segments:
        if count > 0:
            legend_items.append(f'<span><span class="legend-dot" style="background:{color}"></span>{_esc(name)}</span>')
    legend = '<div class="legend">' + " ".join(legend_items) + '</div>' if legend_items else ''
    return f'<div class="bar-section"><div class="bar-label">Threat Categories</div>{bar}{legend}</div>'


def _build_skipped_section(skipped: list[dict]) -> str:
    """Render a notice section for artists that were skipped during scanning.

    Includes a "Retry Skipped Artists" button that calls the retry API endpoint.
    The scan_id is extracted from the page URL (e.g., /report/<scan_id>).
    """
    if not skipped:
        return ""
    rows = []
    for s in skipped:
        name = _esc(s.get("name", "Unknown"))
        reason = _esc(s.get("reason", "Unknown error"))
        rows.append(
            f'<tr><td style="color:#e8eef4;font-weight:500">{name}</td>'
            f'<td style="color:#94a3b8">{reason}</td></tr>'
        )

    count = len(skipped)
    plural = "s" if count != 1 else ""

    return f"""
<div id="skipped-section" style="background:#1a1510;border:1px solid #3d2e1a;border-radius:10px;padding:16px 20px;margin:20px 0">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="color:#f59e0b;font-size:1.2rem">&#9888;</span>
      <span style="color:#f59e0b;font-weight:600;font-size:0.95rem">
        {count} artist{plural} could not be scanned
      </span>
    </div>
    <button id="retryBtn" onclick="retrySkipped()" style="
      background:#f59e0b;color:#000;border:none;padding:8px 18px;border-radius:6px;
      font-weight:700;font-size:0.85rem;cursor:pointer;white-space:nowrap
    ">&#8635; Retry {count} Artist{plural}</button>
  </div>
  <p style="color:#94a3b8;font-size:0.85rem;margin-bottom:10px">
    These artists were skipped due to timeouts or errors during scanning.
    They are not included in the analysis above.
  </p>
  <div id="retryProgress" style="display:none;margin-bottom:12px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <div style="width:14px;height:14px;border:2px solid #f59e0b;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite"></div>
      <span id="retryMsg" style="color:#f59e0b;font-size:0.85rem">Starting retry...</span>
    </div>
    <div style="background:#111820;border-radius:4px;height:6px;overflow:hidden">
      <div id="retryBar" style="width:0%;height:100%;background:#f59e0b;border-radius:4px;transition:width 0.3s"></div>
    </div>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
    <tr style="border-bottom:1px solid #3d2e1a">
      <th style="text-align:left;padding:6px 8px;color:#f59e0b">Artist</th>
      <th style="text-align:left;padding:6px 8px;color:#f59e0b">Reason</th>
    </tr>
    {"".join(rows)}
  </table>
</div>
<style>@keyframes spin {{ to {{ transform: rotate(360deg) }} }}</style>
<script>
function retrySkipped() {{
  var btn = document.getElementById('retryBtn');
  var progress = document.getElementById('retryProgress');
  var msg = document.getElementById('retryMsg');
  var bar = document.getElementById('retryBar');

  // Extract scan_id from URL path: /report/<scan_id>
  var parts = window.location.pathname.split('/');
  var scanId = parts[parts.length - 1] || parts[parts.length - 2];
  if (!scanId) {{ alert('Could not determine scan ID'); return; }}

  btn.disabled = true;
  btn.textContent = 'Retrying...';
  btn.style.opacity = '0.6';
  progress.style.display = 'block';

  fetch('/api/scan/' + scanId + '/retry-skipped', {{ method: 'POST' }})
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      if (data.error) {{ throw new Error(data.error); }}
      var retryId = data.scan_id;
      // Poll for progress
      var poll = setInterval(function() {{
        fetch('/api/scan/' + retryId)
          .then(function(r) {{ return r.json(); }})
          .then(function(s) {{
            if (s.message) msg.textContent = s.message;
            if (s.total > 0) bar.style.width = Math.round(s.current / s.total * 100) + '%';
            if (s.status === 'complete' && s.has_result) {{
              clearInterval(poll);
              msg.textContent = s.message || 'Done!';
              bar.style.width = '100%';
              // Show link to retry results
              progress.innerHTML =
                '<div style="display:flex;align-items:center;gap:10px">' +
                '<span style="color:#22c55e;font-size:1.1rem">&#10003;</span>' +
                '<span style="color:#22c55e;font-weight:600">' + (s.message || 'Retry complete') + '</span>' +
                '<a href="/report/' + retryId + '" style="color:#f59e0b;font-weight:600;margin-left:12px">View Retry Results &rarr;</a>' +
                '</div>';
              btn.style.display = 'none';
            }}
          }})
          .catch(function() {{}});
      }}, 2000);
    }})
    .catch(function(err) {{
      msg.textContent = 'Retry failed: ' + err.message;
      msg.style.color = '#ef4444';
      btn.disabled = false;
      btn.textContent = '\\u21BB Retry {count} Artist{plural}';
      btn.style.opacity = '1';
    }});
}}
</script>"""


# ---------------------------------------------------------------------------
# Artist card builder
# ---------------------------------------------------------------------------

def _build_card(a: ArtistReport, ev: ArtistEvaluation | None, idx: int) -> str:
    """Build a complete artist card (collapsed + expandable detail)."""
    verdict_str = a.verdict
    score = a.final_score
    verdict_color = _VERDICT_COLORS.get(verdict_str, "#94a3b8")
    is_flagged = verdict_str in ("Suspicious", "Likely Artificial")

    # Score badge color (spec Part 1: aligned to updated verdict colors)
    if score >= 82:
        badge_bg = "#22c55e"
    elif score >= 58:
        badge_bg = "#86efac"
    elif score >= 38:
        badge_bg = "#fbbf24"
    elif score >= 18:
        badge_bg = "#f97316"
    else:
        badge_bg = "#ef4444"

    # Collapsed stats line
    stats = _build_stats_line(a, ev)

    # Threat pill
    threat_html = ""
    if a.threat_category_name:
        t_color = _THREAT_COLORS.get(a.threat_category_name, "#888")
        threat_html = f'<span class="threat-pill" style="background:{t_color}22;color:{t_color};border:1px solid {t_color}44">{_esc(a.threat_category_name)}</span>'

    # Card body (detail) — only if we have evaluation data
    body_html = ""
    if ev:
        body_html = _build_card_body(a, ev)

    return f"""<div class="card" data-flagged="{'true' if is_flagged else 'false'}" data-idx="{idx}" data-score="{score}" data-name="{_esc(a.artist_name)}">
  <div class="card-row" onclick="toggleCard(this)">
    <div class="score-badge" style="background:{badge_bg}">{score}</div>
    <div class="card-info">
      <div class="card-name">{_esc(a.artist_name)}</div>
      <div class="card-stats">{stats}</div>
    </div>
    <span class="pill" style="background:{verdict_color}22;color:{verdict_color};border:1px solid {verdict_color}44">{_esc(verdict_str)}</span>
    {threat_html}
    <span class="chevron">&#9660;</span>
  </div>
  <div class="card-body">{body_html}</div>
</div>
"""


def _build_stats_line(a: ArtistReport, ev: ArtistEvaluation | None) -> str:
    """Build the two-line stats summary for collapsed card.

    Line 1: Bad actor check + PRO registry result (search/lookup indicators)
    Line 2: Platform data (Deezer fans, Last.fm, shows, etc.)
    """
    if not ev:
        return ""
    ext = ev.external_data or ExternalData()

    # --- Line 1: Search/lookup indicators ---
    search_parts: list[str] = []

    # Bad actor / blocklist check
    all_tags: set[str] = set()
    for e in ev.red_flags + ev.green_flags:
        if e.tags:
            all_tags.update(e.tags)

    if all_tags & {"entity_confirmed_bad", "known_bad_actor"}:
        search_parts.append('<span style="color:#ef4444">&#9940; Bad actor match</span>')
    elif all_tags & {"pfc_label", "known_ai_label", "pfc_songwriter", "known_ai_artist"}:
        tag_names: list[str] = []
        if all_tags & {"pfc_label", "known_ai_label"}:
            tag_names.append("PFC label")
        if all_tags & {"pfc_songwriter"}:
            tag_names.append("PFC writer")
        if all_tags & {"known_ai_artist"}:
            tag_names.append("AI artist")
        search_parts.append(f'<span style="color:#f59e0b">&#9888; {", ".join(tag_names)}</span>')
    elif all_tags & {"entity_suspected"}:
        search_parts.append('<span style="color:#f59e0b">&#9888; Suspected entity</span>')
    else:
        search_parts.append('<span style="color:#556">&#10003; Blocklist clear</span>')

    # PRO registry (publishing databases)
    if ext.pro_checked:
        pro_names: list[str] = []
        if ext.pro_found_bmi:
            pro_names.append("BMI")
        if ext.pro_found_ascap:
            pro_names.append("ASCAP")
        if pro_names:
            works_str = f", {ext.pro_works_count} works" if ext.pro_works_count else ""
            search_parts.append(f'<span style="color:#22c55e">PRO: {"+".join(pro_names)}{works_str}</span>')
        else:
            search_parts.append('<span style="color:#f59e0b">No PRO registration</span>')

    # --- Line 2: Platform data ---
    data_parts: list[str] = []

    if ev.platform_presence.deezer_fans:
        data_parts.append(f"Deezer fans: {_fmt_num(ev.platform_presence.deezer_fans)}")
    if ext.lastfm_listeners:
        data_parts.append(f"Last.fm: {_fmt_num(ext.lastfm_listeners)} listeners")
    if ext.setlistfm_total_shows:
        data_parts.append(f"{ext.setlistfm_total_shows} shows")
    if ext.discogs_physical_releases:
        data_parts.append(f"{ext.discogs_physical_releases} vinyl/CD")
    if ext.wikipedia_found:
        data_parts.append("Wikipedia")

    # Fallback
    if not data_parts:
        data_parts.append(f"{len(ev.green_flags)} green / {len(ev.red_flags)} red flags")

    # Labels
    if ev.labels:
        data_parts.append(_esc(ev.labels[0]))

    line1 = " &middot; ".join(search_parts)
    line2 = " &middot; ".join(data_parts[:5])
    return f'{line1}<br>{line2}'


def _build_card_body(a: ArtistReport, ev: ArtistEvaluation) -> str:
    """Build the expanded card body organized by 6 signal axes.

    Layout:
    1. Scorecard: Radar chart (left) + summary metrics area (right)
       - Row 1: Verdict + Confidence
       - Row 2: Platform Icons Row
       - Row 3: Key Stats (scrobbles, fans, concerts, releases)
    2. Six-axis bucket grid with evidence
    3. AI analysis (if available)
    """
    ext = ev.external_data or ExternalData()
    verdict_str = ev.verdict.value
    verdict_color = _VERDICT_COLORS.get(verdict_str, "#94a3b8")
    scores = ev.category_scores

    # 1. Explanation
    explanation = _build_explanation(ev)
    explanation_html = (
        f'<div class="explanation" style="background:{verdict_color}0d;border-left:3px solid {verdict_color}">'
        f'{_esc(explanation)}</div>'
    )

    # 2. Scorecard: Radar chart + summary metrics
    radar_html = _radar_svg(scores, verdict_color, size=240)
    platform_icons_html = _build_platform_icons(ev, ext)
    key_stats_html = _build_key_stats(ev, ext)

    scorecard_html = f"""<div class="scorecard">
  <div>{radar_html}</div>
  <div>
    <div style="margin-bottom:12px">
      <span class="score-badge" style="background:{verdict_color};display:inline-flex;width:32px;height:32px;font-size:0.8rem">{a.final_score}</span>
      <span style="color:{verdict_color};font-weight:600;margin-left:8px">{_esc(verdict_str)}</span>
      <span style="color:#667;font-size:0.78rem;margin-left:8px">{_esc(ev.confidence)} confidence</span>
    </div>
    {platform_icons_html}
    {key_stats_html}
  </div>
</div>"""

    # 3. Six-axis bucket grid
    buckets_html = _build_axis_buckets(ev, ext, scores)

    # 4. AI analysis (if available)
    ai_html = ""
    for sig in a.deep_signals:
        if isinstance(sig, dict) and sig.get("detail"):
            ai_html = (
                f'<div class="explanation" style="background:#1a2332;border-left:3px solid #a78bfa">'
                f'<div style="font-size:0.72rem;color:#a78bfa;text-transform:uppercase;margin-bottom:6px">'
                f'AI Analysis</div>{_esc(sig["detail"])}</div>'
            )
            break

    return f"""
    {explanation_html}
    {scorecard_html}
    {buckets_html}
    {ai_html}
    """


def _build_platform_icons(ev: ArtistEvaluation, ext: ExternalData) -> str:
    """Build the Platform Icons Row showing found/not-found for each platform."""
    platforms = [
        ("Deezer", ev.platform_presence.deezer),
        ("MusicBrainz", ext.musicbrainz_found),
        ("Genius", ext.genius_found),
        ("Last.fm", ext.lastfm_found),
        ("Discogs", ext.discogs_found),
        ("Setlist.fm", ext.setlistfm_found),
        ("YouTube", ext.youtube_channel_found if ext.youtube_checked else None),
        ("Wikipedia", ext.wikipedia_found),
    ]

    badges = []
    for name, found in platforms:
        if found is None:
            # Not checked
            icon = '<span style="color:#333">&#8226;</span>'
            color = "#333"
        elif found:
            icon = '<span style="color:#22c55e">&#10003;</span>'
            color = "#22c55e"
        else:
            icon = '<span style="color:#444">&#10007;</span>'
            color = "#444"
        badges.append(
            f'<span style="display:inline-flex;align-items:center;gap:3px;'
            f'padding:2px 6px;border:1px solid {color}33;border-radius:4px;'
            f'font-size:0.7rem;color:{color}">'
            f'{icon} {_esc(name)}</span>'
        )
    return (
        '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px">'
        + "".join(badges)
        + '</div>'
    )


def _build_key_stats(ev: ArtistEvaluation, ext: ExternalData) -> str:
    """Build the Key Stats row with 3-4 compact stat boxes."""
    stats: list[tuple[str, str]] = []

    # Last.fm scrobbles
    if ext.lastfm_playcount:
        stats.append((_fmt_num(ext.lastfm_playcount), "Scrobbles"))

    # Deezer fans
    if ev.platform_presence.deezer_fans:
        stats.append((_fmt_num(ev.platform_presence.deezer_fans), "Deezer Fans"))

    # Concerts (best of Setlist.fm or Songkick)
    shows = ext.setlistfm_total_shows or ext.songkick_total_past_events
    if shows:
        stats.append((str(shows), "Shows"))

    # Releases
    # We don't have album/single breakdown in ext directly, but we can
    # use discogs totals or other catalog data
    if ext.discogs_total_releases:
        parts = []
        if ext.discogs_physical_releases:
            parts.append(f"{ext.discogs_physical_releases} physical")
        if ext.discogs_digital_releases:
            parts.append(f"{ext.discogs_digital_releases} digital")
        if parts:
            stats.append((", ".join(parts), "Releases"))
        else:
            stats.append((str(ext.discogs_total_releases), "Releases"))

    if not stats:
        return ""

    boxes = []
    for value, label in stats[:4]:
        boxes.append(
            f'<div class="metric-card" style="padding:8px;min-width:80px">'
            f'<div class="metric-value" style="font-size:1rem">{_esc(value)}</div>'
            f'<div class="metric-label">{_esc(label)}</div>'
            f'</div>'
        )
    return '<div style="display:flex;gap:8px;flex-wrap:wrap">' + "".join(boxes) + '</div>'


# ---------------------------------------------------------------------------
# Axis bucket system — maps evidence + data to the 6 radar dimensions
# ---------------------------------------------------------------------------

# Tag-to-axis classification for evidence flags
# v0.9: Aligned with ArtistCard.jsx — physical_release → IRL Presence,
# AI/fraud indicators → Blocklist Status, bio/photo → Platform Presence.
_TAG_TO_AXIS: dict[str, str] = {
    # Platform Presence — where the artist exists across the music ecosystem
    "platform_presence": "Platform Presence",
    "not_found": "Platform Presence",
    "multi_platform": "Platform Presence",
    "single_platform": "Platform Presence",
    "wikipedia": "Platform Presence",
    "social_media": "Platform Presence",
    "no_social_media": "Platform Presence",
    "youtube_presence": "Platform Presence",
    "no_youtube": "Platform Presence",
    "verified_identity": "Platform Presence",
    "genius_verified": "Platform Presence",
    "bandcamp_presence": "Platform Presence",
    "authentic_bio": "Platform Presence",
    "authentic_photo": "Platform Presence",
    "generic_name": "Platform Presence",
    "press_coverage": "Platform Presence",
    # Fan Engagement — real fan activity vs algorithmic/passive
    "genuine_fans": "Fan Engagement",
    "low_fans": "Fan Engagement",
    "low_engagement": "Fan Engagement",
    "low_scrobble_engagement": "Fan Engagement",
    "streaming_pattern": "Fan Engagement",
    "youtube_disparity": "Fan Engagement",
    "listener_playlist_ratio": "Fan Engagement",
    # Creative History — evidence of genuine artistic output
    "catalog_albums": "Creative History",
    "genius_credits": "Creative History",
    "collaboration": "Creative History",
    "content_farm": "Creative History",
    "stream_farm": "Creative History",
    "empty_catalog": "Creative History",
    "cookie_cutter": "Creative History",
    "high_release_rate": "Creative History",
    "same_day_release": "Creative History",
    # IRL Presence — physical-world evidence of the artist
    "live_performance": "IRL Presence",
    "concert_history": "IRL Presence",
    "physical_release": "IRL Presence",
    # Industry Signals — professional music industry registration and bios
    "industry_registered": "Industry Signals",
    "pro_registered": "Industry Signals",
    "no_pro_registration": "Industry Signals",
    "normal_pro_split": "Industry Signals",
    "no_songwriter_share": "Industry Signals",
    "career_bio": "Industry Signals",
    "ai_bio": "Industry Signals",
    "suspicious_bio": "Industry Signals",
    "impersonation": "Industry Signals",
    "cowriter_network": "Industry Signals",
    # Blocklist Status — matches against known fraud databases
    "pfc_label": "Blocklist Status",
    "pfc_songwriter": "Blocklist Status",
    "pfc_publisher": "Blocklist Status",
    "known_ai_artist": "Blocklist Status",
    "known_ai_label": "Blocklist Status",
    "known_bad_actor": "Blocklist Status",
    "entity_confirmed_bad": "Blocklist Status",
    "entity_suspected": "Blocklist Status",
    "entity_cleared": "Blocklist Status",
    "entity_bad_label": "Blocklist Status",
    "entity_bad_songwriter": "Blocklist Status",
    "entity_bad_network": "Blocklist Status",
    "isrc_pfc_registrant": "Blocklist Status",
    "ai_generated_image": "Blocklist Status",
    "ai_generated_music": "Blocklist Status",
    "stock_photo": "Blocklist Status",
    "deezer_ai_clear": "Blocklist Status",
}

# Fallback: classify by evidence source name
_SOURCE_TO_AXIS: list[tuple[str, str]] = [
    ("deezer", "Fan Engagement"),
    ("last.fm", "Fan Engagement"),
    ("lastfm", "Fan Engagement"),
    ("genius", "Creative History"),
    ("discogs", "IRL Presence"),
    ("setlist", "IRL Presence"),
    ("songkick", "IRL Presence"),
    ("wikipedia", "Platform Presence"),
    ("youtube", "Platform Presence"),
    ("musicbrainz", "Industry Signals"),
    ("pro ", "Industry Signals"),
    ("blocklist", "Blocklist Status"),
    ("entity", "Blocklist Status"),
    ("pre-check", "Blocklist Status"),
]


def _classify_evidence(e: Evidence) -> str:
    """Assign an evidence item to one of the 6 signal axes."""
    if e.tags:
        for tag in e.tags:
            if tag in _TAG_TO_AXIS:
                return _TAG_TO_AXIS[tag]
    src = e.source.lower()
    for keyword, axis in _SOURCE_TO_AXIS:
        if keyword in src:
            return axis
    return "Platform Presence"


# Padding candidates — used when a section has < 2 real signals.
# Only added if the data source is absent from ALL evidence.
_PAD_CANDIDATES: dict[str, list[tuple[str, str]]] = {
    "Platform Presence": [
        ("Deezer", "Not found on Deezer"),
        ("MusicBrainz", "Not found on MusicBrainz"),
        ("Genius", "Not found on Genius"),
    ],
    "Fan Engagement": [
        ("Last.fm", "No Last.fm listener data found"),
        ("YouTube", "No YouTube engagement data found"),
    ],
    "Creative History": [],
    "IRL Presence": [
        ("Setlist.fm", "No concerts found on Setlist.fm"),
        ("Discogs", "No physical releases found on Discogs"),
    ],
    "Industry Signals": [
        ("PRO Registry", "No PRO registration found"),
    ],
    "Blocklist Status": [
        ("Blocklist", "Not matched on any blocklists"),
        ("Entity DB", "No prior intelligence in entity database"),
    ],
}


def _get_pad_items(axis: str, all_sources: set[str]) -> list[str]:
    """Return pad findings for thin sections, skipping sources already present."""
    candidates = _PAD_CANDIDATES.get(axis, [])
    return [finding for src, finding in candidates if src not in all_sources]


def _build_axis_buckets(ev: ArtistEvaluation, ext: ExternalData, scores: dict[str, int]) -> str:
    """Build a 2x3 grid of axis buckets, each with score bar + signals.

    Each bucket shows top green signals first, then top red signals,
    up to 5 total. Thin sections are padded with "not found" items.
    """
    # Classify evidence into axes (exclude weak signals)
    axis_greens: dict[str, list[Evidence]] = {name: [] for name in scores}
    axis_reds: dict[str, list[Evidence]] = {name: [] for name in scores}
    for e in ev.red_flags + ev.green_flags:
        if e.strength == "weak":
            continue
        axis = _classify_evidence(e)
        if axis not in scores:
            continue
        if e.evidence_type == "red_flag":
            axis_reds[axis].append(e)
        else:
            axis_greens[axis].append(e)

    # Also add data-derived signals (from ext) as synthetic items
    _inject_data_signals(axis_greens, axis_reds, ev, ext)

    # Collect all evidence sources for padding logic
    all_sources: set[str] = set()
    for e in ev.red_flags + ev.green_flags:
        all_sources.add(e.source)

    axis_order = [
        "Platform Presence", "Fan Engagement", "Creative History",
        "IRL Presence", "Industry Signals", "Blocklist Status",
    ]
    axis_icons = {
        "Platform Presence": "&#127760;",   # globe
        "Fan Engagement": "&#128101;",      # people
        "Creative History": "&#127925;",    # music note
        "IRL Presence": "&#127970;",        # venue
        "Industry Signals": "&#127917;",    # drama masks
        "Blocklist Status": "&#128737;",    # shield
    }
    strength_order = {"strong": 0, "moderate": 1}

    buckets: list[str] = []
    for axis in axis_order:
        score = scores.get(axis, 0)
        # 4-tier color per spec Part 5
        if axis == "Blocklist Status":
            color = "#22c55e" if score >= 100 else "#ef4444"
        elif score >= 70:
            color = "#22c55e"
        elif score >= 40:
            color = "#86efac"
        elif score >= 15:
            color = "#f97316"
        else:
            color = "#ef4444" if score > 0 else "#9ca3af"
        icon = axis_icons.get(axis, "")

        greens = sorted(axis_greens.get(axis, []), key=lambda e: strength_order.get(e.strength, 2))
        reds = sorted(axis_reds.get(axis, []), key=lambda e: strength_order.get(e.strength, 2))

        # Top greens first, then top reds, up to 5 total
        items_html = ""
        shown = 0
        for e in greens:
            if shown >= 5:
                break
            items_html += (
                f'<div class="axis-item">'
                f'<span class="axis-icon" style="color:#22c55e">&#10003;</span>'
                f'{_esc(e.finding)}</div>'
            )
            shown += 1
        for e in reds:
            if shown >= 5:
                break
            items_html += (
                f'<div class="axis-item">'
                f'<span class="axis-icon" style="color:#ef4444">&#10007;</span>'
                f'{_esc(e.finding)}</div>'
            )
            shown += 1

        # Pad thin sections to at least 2 items with "not found" entries
        if shown < 2:
            pad_items = _get_pad_items(axis, all_sources)
            for finding in pad_items:
                if shown >= 3:
                    break
                is_positive = axis == "Blocklist Status"
                pad_color = "#22c55e" if is_positive else "#556"
                pad_icon = "&#10003;" if is_positive else "&#8226;"
                items_html += (
                    f'<div class="axis-item">'
                    f'<span class="axis-icon" style="color:{pad_color}">{pad_icon}</span>'
                    f'<span style="color:#556">{_esc(finding)}</span></div>'
                )
                shown += 1

        if not items_html:
            items_html = '<div class="axis-item"><span style="color:#445">No data</span></div>'

        # Special treatment for Creative History: add release timeline
        timeline_html = ""
        if axis == "Creative History" and ext.release_year_summary:
            timeline_html = _build_release_timeline(ext.release_year_summary)

        # Special treatment for Blocklist Status: show banner
        banner_html = ""
        if axis == "Blocklist Status":
            if not reds:
                banner_html = (
                    '<div style="background:#22c55e15;border:1px solid #22c55e33;'
                    'border-radius:4px;padding:6px 10px;margin-bottom:6px;'
                    'font-size:0.78rem;color:#22c55e;font-weight:600">'
                    '&#10003; Clean across all blocklists</div>'
                )
            else:
                banner_html = (
                    f'<div style="background:#ef444415;border:1px solid #ef444433;'
                    f'border-radius:4px;padding:6px 10px;margin-bottom:6px;'
                    f'font-size:0.78rem;color:#ef4444;font-weight:600">'
                    f'&#9888; {len(reds)} blocklist hit{"s" if len(reds) != 1 else ""}</div>'
                )

        buckets.append(f"""<div class="axis-bucket">
  <div class="axis-header">
    <span class="axis-name">{icon} {_esc(axis)}</span>
    <span class="axis-score" style="color:{color}">{score}</span>
  </div>
  <div class="axis-bar"><div class="axis-bar-fill" style="width:{score}%;background:{color}"></div></div>
  {banner_html}
  {timeline_html}
  {items_html}
</div>""")

    return '<div class="axis-grid">' + "\n".join(buckets) + '</div>'


def _build_release_timeline(year_summary: dict[int, dict[str, int]]) -> str:
    """Render a compact per-year release timeline for Creative History."""
    if not year_summary:
        return ""

    sorted_years = sorted(year_summary.keys())
    if len(sorted_years) < 1:
        return ""

    max_count = max(
        (d.get("releases", 0) or (d.get("albums", 0) + d.get("singles", 0)))
        for d in year_summary.values()
    )
    if max_count == 0:
        return ""

    rows = []
    for year in sorted_years:
        data = year_summary[year]
        count = data.get("releases", 0) or (data.get("albums", 0) + data.get("singles", 0))
        bar_width = int(count / max_count * 100) if max_count else 0
        # Color: normal = green, high (>6) = amber, extreme (>12) = red
        bar_color = "#22c55e" if count <= 6 else "#f59e0b" if count <= 12 else "#ef4444"
        rows.append(
            f'<div style="display:flex;align-items:center;gap:6px;font-size:0.72rem">'
            f'<span style="width:32px;color:#667;text-align:right">{year}</span>'
            f'<div style="flex:1;height:6px;background:#111820;border-radius:3px;overflow:hidden">'
            f'<div style="width:{bar_width}%;height:100%;background:{bar_color};border-radius:3px"></div></div>'
            f'<span style="width:24px;color:#889">{count}</span>'
            f'</div>'
        )

    return (
        '<div style="margin:6px 0 8px;padding:6px 0;border-top:1px solid #1a2332">'
        '<div style="font-size:0.68rem;color:#556;text-transform:uppercase;margin-bottom:4px">'
        'Releases by Year</div>'
        + "\n".join(rows)
        + '</div>'
    )


def _inject_data_signals(
    greens: dict[str, list[Evidence]],
    reds: dict[str, list[Evidence]],
    ev: ArtistEvaluation,
    ext: ExternalData,
) -> None:
    """Inject concise data-derived signals into axis buckets.

    These supplement the evidence flags with concrete numbers from the
    API responses, formatted as short signal lines.
    """
    def _green(axis: str, finding: str) -> None:
        greens[axis].append(Evidence(
            finding=finding, source="data", evidence_type="green_flag",
            strength="moderate", detail="",
        ))

    def _red(axis: str, finding: str) -> None:
        reds[axis].append(Evidence(
            finding=finding, source="data", evidence_type="red_flag",
            strength="moderate", detail="",
        ))

    # Platform Presence (now includes YouTube, Wikipedia, social media)
    found = sum(1 for r in ev.sources_reached.values() if r)
    total = len(ev.sources_reached)
    if found >= 4:
        _green("Platform Presence", f"Found on {found}/{total} platforms")
    elif found <= 1:
        _red("Platform Presence", f"Only {found}/{total} platforms")

    if ext.wikipedia_found:
        if ext.wikipedia_monthly_views:
            _green("Platform Presence", f"Wikipedia ({ext.wikipedia_monthly_views:,} views/mo)")
        else:
            _green("Platform Presence", "Wikipedia page exists")
    if ext.youtube_channel_found:
        if ext.youtube_subscriber_count:
            _green("Platform Presence", f"YouTube ({ext.youtube_subscriber_count:,} subs)")
        else:
            _green("Platform Presence", "YouTube channel exists")
    if ext.genius_is_verified:
        _green("Platform Presence", "Genius verified artist")

    # Fan Engagement
    fans = ev.platform_presence.deezer_fans or 0
    if fans >= 10_000:
        _green("Fan Engagement", f"Deezer: {fans:,} fans")
    elif fans == 0:
        _red("Fan Engagement", "Deezer: 0 fans")

    if ext.lastfm_listeners and ext.lastfm_listeners >= 1_000:
        _green("Fan Engagement", f"Last.fm: {ext.lastfm_listeners:,} listeners")

    # Creative History
    if ext.discogs_physical_releases:
        _green("Creative History", f"{ext.discogs_physical_releases} physical releases (Discogs)")
    if ext.genius_song_count and ext.genius_song_count >= 5:
        _green("Creative History", f"{ext.genius_song_count} songs on Genius")

    # IRL Presence (renamed from Live Performance)
    if ext.setlistfm_total_shows:
        _green("IRL Presence", f"{ext.setlistfm_total_shows} shows (Setlist.fm)")
    if ext.songkick_total_past_events:
        _green("IRL Presence", f"{ext.songkick_total_past_events} events (Songkick)")
    countries = ext.setlistfm_venue_countries or ext.songkick_venue_countries
    if countries and len(countries) >= 3:
        _green("IRL Presence", f"Toured {len(countries)} countries")
    if ext.discogs_physical_releases:
        _green("IRL Presence", f"{ext.discogs_physical_releases} vinyl/CD (Discogs)")

    # Industry Signals (now includes Discogs bio, real name)
    if ext.musicbrainz_isnis:
        _green("Industry Signals", "ISNI registered")
    if ext.musicbrainz_ipis:
        _green("Industry Signals", "IPI registered")
    if ext.pro_checked:
        pro: list[str] = []
        if ext.pro_found_bmi:
            pro.append("BMI")
        if ext.pro_found_ascap:
            pro.append("ASCAP")
        if pro:
            works_label = f" ({ext.pro_works_count} works)" if ext.pro_works_count else ""
            share_label = ""
            if ext.pro_songwriter_share_pct >= 0:
                share_label = f", {ext.pro_songwriter_share_pct:.0f}% writer share"
            _green("Industry Signals", f"PRO: {'+'.join(pro)}{works_label}{share_label}")
        else:
            _red("Industry Signals", "Not in BMI or ASCAP")
    if ext.discogs_realname:
        _green("Industry Signals", f"Real name: {ext.discogs_realname}")
    if len(ext.discogs_profile) >= 200:
        _green("Industry Signals", "Detailed Discogs bio")

    # Blocklist Status (new category)
    all_tags: set[str] = set()
    for e in ev.red_flags + ev.green_flags:
        if e.tags:
            all_tags.update(e.tags)
    blocklist_hits = all_tags & {
        "pfc_label", "pfc_songwriter", "known_ai_artist", "known_ai_label",
        "entity_confirmed_bad", "entity_suspected", "pfc_publisher",
    }
    if not blocklist_hits:
        _green("Blocklist Status", "Clean across all blocklists")


def _build_explanation(ev: ArtistEvaluation) -> str:
    """Generate a plain-English explanation of the verdict."""
    name = ev.artist_name
    verdict = ev.verdict
    platforms = ev.platform_presence.count()
    fans = ev.platform_presence.deezer_fans
    red_count = len(ev.red_flags)
    green_count = len(ev.green_flags)
    strong_reds = len(ev.strong_red_flags)

    if verdict == Verdict.VERIFIED_ARTIST:
        parts = [f"{name} looks like a real, established artist."]
        if platforms >= 5:
            parts.append(f"Found on {platforms} different music platforms.")
        if fans >= 100_000:
            parts.append(f"{fans:,} fans on Deezer.")
        if green_count >= 10:
            parts.append(f"{green_count} positive signals and no serious concerns.")
        return " ".join(parts)

    elif verdict == Verdict.LIKELY_AUTHENTIC:
        parts = [f"{name} appears to be a legitimate artist."]
        if platforms >= 3:
            parts.append(f"Found on {platforms} platforms with mostly positive signals.")
        parts.append(f"{green_count} green flags and {red_count} red flags.")
        return " ".join(parts)

    elif verdict == Verdict.INSUFFICIENT_DATA:
        parts = [f"Not enough data to evaluate {name}."]
        total = green_count + red_count
        parts.append(f"Only {total} signal{'s' if total != 1 else ''} collected.")
        parts.append("This often happens with brand-new or very niche artists.")
        return " ".join(parts)

    elif verdict == Verdict.CONFLICTING_SIGNALS:
        parts = [f"The evidence on {name} is contradictory."]
        parts.append(f"{green_count} positive and {red_count} negative signals, both substantial.")
        parts.append("This can happen with real artists on PFC-associated labels.")
        return " ".join(parts)

    elif verdict == Verdict.INCONCLUSIVE:
        parts = [f"Couldn't make a confident determination about {name}."]
        parts.append(f"Mixed evidence: {green_count} positive and {red_count} negative signals.")
        return " ".join(parts)

    elif verdict == Verdict.SUSPICIOUS:
        parts = [f"{name} shows several warning signs."]
        if strong_reds:
            parts.append(f"{strong_reds} strong red flag{'s' if strong_reds != 1 else ''}.")
        if platforms <= 2:
            parts.append(f"Only found on {platforms} platform{'s' if platforms != 1 else ''}.")
        parts.append("Pattern warrants scrutiny.")
        return " ".join(parts)

    elif verdict == Verdict.LIKELY_ARTIFICIAL:
        parts = [f"{name} has strong indicators of being artificial or manufactured."]
        if strong_reds >= 3:
            parts.append(f"{strong_reds} strong red flags.")
        for e in ev.red_flags:
            if e.tags and {"pfc_label", "content_farm"} & set(e.tags):
                parts.append("Release pattern and distributor match known content farm operations.")
                break
        return " ".join(parts)

    return f"Evaluated {name}: {green_count} green flags, {red_count} red flags."


def _build_signal_bars(scores: dict[str, int]) -> str:
    """Render signal bars for the 6 radar categories."""
    rows = []
    for cat, val in scores.items():
        # 4-tier color per spec Part 5
        if cat == "Blocklist Status":
            color = "#22c55e" if val >= 100 else "#ef4444"
        elif val >= 70:
            color = "#22c55e"
        elif val >= 40:
            color = "#86efac"
        elif val >= 15:
            color = "#f97316"
        else:
            color = "#ef4444" if val > 0 else "#9ca3af"
        rows.append(
            f'<div class="signal-row">'
            f'<span class="signal-label">{_esc(cat)}</span>'
            f'<div class="signal-track"><div class="signal-fill" style="width:{val}%;background:{color}"></div></div>'
            f'<span class="signal-val">{val}</span>'
            f'</div>'
        )
    return '<div class="signal-bars">' + "\n".join(rows) + '</div>'


def _build_sources_grid(ev: ArtistEvaluation, ext: ExternalData) -> str:
    """Render data sources grid showing which APIs returned data."""
    sources = ev.sources_reached
    items = []
    for name, reached in sources.items():
        if reached:
            icon = '<span style="color:#22c55e">&#9679;</span>'
        else:
            icon = '<span style="color:#333">&#9679;</span>'
        # Match quality
        mk = name.lower().replace(".", "").replace(" ", "")
        key_map = {"deezer": "deezer", "genius": "genius", "discogs": "discogs",
                    "setlistfm": "setlistfm", "musicbrainz": "musicbrainz",
                    "lastfm": "lastfm", "wikipedia": "wikipedia", "songkick": "songkick"}
        mk = key_map.get(mk, mk)
        method = ext.match_methods.get(mk, "")
        badge = ""
        if method == "platform_id":
            badge = ' <span style="color:#556;font-size:0.6rem">ID</span>'
        elif method and ext.match_confidences.get(mk, 0) > 0:
            conf = ext.match_confidences[mk]
            badge = f' <span style="color:#556;font-size:0.6rem">{conf:.0%}</span>'
        items.append(f'<span style="font-size:0.78rem">{icon} {_esc(name)}{badge}</span>')

    return '<div style="display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:8px">' + " ".join(items) + '</div>'


def _build_metadata_grid(a: ArtistReport, ev: ArtistEvaluation, ext: ExternalData) -> str:
    """Build a 2-column key-value metadata grid."""
    items: list[tuple[str, str]] = []

    # Fan counts
    if ev.platform_presence.deezer_fans:
        items.append(("Deezer fans", _fmt_num(ev.platform_presence.deezer_fans)))
    if ext.lastfm_listeners:
        items.append(("Last.fm listeners", _fmt_num(ext.lastfm_listeners)))
    if ext.lastfm_playcount:
        items.append(("Last.fm plays", _fmt_num(ext.lastfm_playcount)))
    if ext.lastfm_listener_play_ratio:
        items.append(("Play/listener ratio", f"{ext.lastfm_listener_play_ratio:.1f}x"))

    # Catalog
    if ext.genius_song_count:
        items.append(("Genius songs", str(ext.genius_song_count)))
    if ext.discogs_physical_releases:
        items.append(("Physical releases", str(ext.discogs_physical_releases)))
    if ext.discogs_total_releases:
        items.append(("Discogs releases", str(ext.discogs_total_releases)))

    # Identity
    if ext.musicbrainz_type:
        items.append(("Type", ext.musicbrainz_type))
    if ext.musicbrainz_country:
        items.append(("Country", ext.musicbrainz_country))
    if ext.musicbrainz_begin_date:
        items.append(("Active since", ext.musicbrainz_begin_date))
    if ext.discogs_realname:
        items.append(("Real name", ext.discogs_realname))
    if ext.musicbrainz_genres:
        items.append(("Genres", ", ".join(ext.musicbrainz_genres[:4])))

    # Labels
    if ev.labels:
        items.append(("Labels", ", ".join(ev.labels[:3])))

    # Live
    if ext.setlistfm_total_shows:
        items.append(("Setlist.fm shows", str(ext.setlistfm_total_shows)))
    if ext.setlistfm_venue_countries:
        items.append(("Tour countries", ", ".join(ext.setlistfm_venue_countries[:5])))
    if ext.songkick_total_past_events:
        items.append(("Songkick events", str(ext.songkick_total_past_events)))

    # YouTube
    if ext.youtube_channel_found:
        if ext.youtube_subscriber_count:
            items.append(("YouTube subs", _fmt_num(ext.youtube_subscriber_count)))

    # Wikipedia
    if ext.wikipedia_found and ext.wikipedia_monthly_views:
        items.append(("Wikipedia views/mo", _fmt_num(ext.wikipedia_monthly_views)))

    if not items:
        return ""

    cells = "".join(
        f'<div class="meta-item"><span class="meta-key">{_esc(k)}:</span> <span class="meta-val">{_esc(v)}</span></div>'
        for k, v in items
    )
    return f'<div class="meta-grid">{cells}</div>'


def _build_evidence_section(ev: ArtistEvaluation) -> str:
    """Build the evidence breakdown with red/green columns and point totals."""
    # Sort by strength descending
    strength_order = {"strong": 0, "moderate": 1, "weak": 2}
    reds = sorted(ev.red_flags, key=lambda e: strength_order.get(e.strength, 3))
    greens = sorted(ev.green_flags, key=lambda e: strength_order.get(e.strength, 3))

    # Point totals
    red_pts = sum(_STRENGTH_PTS.get(e.strength, 1) for e in reds)
    green_pts = sum(_STRENGTH_PTS.get(e.strength, 1) for e in greens)

    def _count_by_strength(flags: list[Evidence]) -> str:
        s = sum(1 for e in flags if e.strength == "strong")
        m = sum(1 for e in flags if e.strength == "moderate")
        w = sum(1 for e in flags if e.strength == "weak")
        parts = []
        if s:
            parts.append(f"{s} strong")
        if m:
            parts.append(f"{m} moderate")
        if w:
            parts.append(f"{w} weak")
        return ", ".join(parts)

    # Red flags column
    red_items = ""
    for e in reds:
        dots = _strength_dots(e.strength, "#ef4444")
        red_items += f"""<div class="flag-item">
  <div class="flag-finding">{dots} {_esc(e.finding)}</div>
  <div class="flag-detail">{_esc(e.detail)}</div>
  <div class="flag-meta">Source: {_esc(e.source)}</div>
</div>"""

    # Green flags column
    green_items = ""
    for e in greens:
        dots = _strength_dots(e.strength, "#22c55e")
        green_items += f"""<div class="flag-item">
  <div class="flag-finding">{dots} {_esc(e.finding)}</div>
  <div class="flag-detail">{_esc(e.detail)}</div>
  <div class="flag-meta">Source: {_esc(e.source)}</div>
</div>"""

    pts_line = (
        f'<div class="pts-summary">'
        f'<span style="color:#ef4444">Red: {red_pts} pts</span> ({_count_by_strength(reds)}) '
        f'&nbsp;|&nbsp; '
        f'<span style="color:#22c55e">Green: {green_pts} pts</span> ({_count_by_strength(greens)})'
        f'</div>'
    )

    return f"""<div class="evidence-section">
  {pts_line}
  <div class="evidence-cols">
    <div>
      <div class="evidence-col-header" style="color:#ef4444">Red Flags ({len(reds)})</div>
      {red_items if red_items else '<div style="color:#445;font-size:0.82rem;padding:8px 0">None</div>'}
    </div>
    <div>
      <div class="evidence-col-header" style="color:#22c55e">Green Flags ({len(greens)})</div>
      {green_items if green_items else '<div style="color:#445;font-size:0.82rem;padding:8px 0">None</div>'}
    </div>
  </div>
</div>"""


def _build_entities_section(ev: ArtistEvaluation, ext: ExternalData) -> str:
    """Build connected entities section from entity DB evidence."""
    entity_items = []
    for e in ev.red_flags + ev.green_flags:
        if not e.tags:
            continue
        tag_set = set(e.tags)
        if tag_set & {"entity_confirmed_bad", "entity_suspected", "entity_bad_label",
                       "entity_bad_songwriter", "entity_bad_network"}:
            is_confirmed = "entity_confirmed_bad" in tag_set
            icon = "&#9940;" if is_confirmed else "&#9888;"
            color = "#ef4444" if is_confirmed else "#f59e0b"
            entity_items.append(
                f'<div class="entity-item">'
                f'<span style="color:{color}">{icon}</span> {_esc(e.finding)}'
                f'</div>'
            )
        elif "entity_cleared" in tag_set:
            entity_items.append(
                f'<div class="entity-item">'
                f'<span style="color:#22c55e">&#10003;</span> {_esc(e.finding)}'
                f'</div>'
            )

    if not entity_items:
        return ""

    return (
        '<div class="entities">'
        '<div style="font-size:0.72rem;color:#667;text-transform:uppercase;margin-bottom:6px">Connected Entities</div>'
        + "".join(entity_items)
        + '</div>'
    )
