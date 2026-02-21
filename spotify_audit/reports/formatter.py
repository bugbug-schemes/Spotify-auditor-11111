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

from spotify_audit.config import THREAT_CATEGORIES, score_label
from spotify_audit.scoring import PlaylistReport, ArtistReport
from spotify_audit.evidence import ArtistEvaluation, Evidence, Verdict, ExternalData


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def to_json(report: PlaylistReport) -> str:
    """Serialize the full playlist report to JSON."""
    return json.dumps(_report_to_dict(report), indent=2, default=str)


def _report_to_dict(report: PlaylistReport) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "playlist": {
            "name": report.playlist_name,
            "id": report.playlist_id,
            "owner": report.owner,
            "total_tracks": report.total_tracks,
            "total_unique_artists": report.total_unique_artists,
            "is_spotify_owned": report.is_spotify_owned,
            "health_score": report.health_score,
        },
        "verdict_summary": {
            "verified_artists": report.verified_artists,
            "likely_authentic": report.likely_authentic,
            "inconclusive": report.inconclusive,
            "suspicious": report.suspicious,
            "likely_artificial": report.likely_artificial,
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
        d["platform_presence"] = ev.platform_presence.names()
        d["category_scores"] = ev.category_scores
        d["sources_reached"] = ev.sources_reached
        d["decision_path"] = ev.decision_path
        d["red_flags"] = [_evidence_to_dict(e) for e in ev.red_flags]
        d["green_flags"] = [_evidence_to_dict(e) for e in ev.green_flags]
        d["neutral_notes"] = [_evidence_to_dict(e) for e in ev.neutral_notes]
        if ev.labels:
            d["labels"] = ev.labels
        if ev.contributors:
            d["contributors"] = ev.contributors

        # Full external data snapshot
        ext = ev.external_data
        if ext:
            d["external_data"] = _external_data_to_dict(ext)

    # Legacy score data (supplementary)
    if a.quick_score is not None:
        d["legacy_quick_score"] = a.quick_score
    if a.standard_score is not None:
        d["legacy_standard_score"] = a.standard_score
    if a.quick_signals:
        d["quick_signals"] = a.quick_signals

    return d


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
        d["pro_registry"] = {
            "checked": True, "bmi": ext.pro_found_bmi, "ascap": ext.pro_found_ascap,
            "works": ext.pro_works_count, "publishers": ext.pro_publishers,
            "songwriter_registered": ext.pro_songwriter_registered,
            "pfc_publisher_match": ext.pro_pfc_publisher_match,
        }
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
    return {
        "finding": e.finding,
        "source": e.source,
        "type": e.evidence_type,
        "strength": e.strength,
        "detail": e.detail,
    }


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
    "Likely Authentic": "#3b82f6",
    "Inconclusive": "#94a3b8",
    "Insufficient Data": "#94a3b8",
    "Conflicting Signals": "#94a3b8",
    "Suspicious": "#f59e0b",
    "Likely Artificial": "#ef4444",
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

_VERDICT_HEALTH = {
    "Verified Artist": 100, "Likely Authentic": 85, "Inconclusive": 50,
    "Insufficient Data": 50, "Conflicting Signals": 50,
    "Suspicious": 25, "Likely Artificial": 0,
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
    contamination = (flagged / report.total_unique_artists * 100) if report.total_unique_artists else 0
    total_api_calls = sum(report.api_source_counts.values()) if report.api_source_counts else 0

    # Build artist cards
    artist_cards_html = []
    for idx, a in enumerate(sorted_artists):
        ev = a.evaluation
        artist_cards_html.append(_build_card(a, ev, idx))

    # Verdict bar segments
    verdict_segments = [
        ("Verified Artist", report.verified_artists, "#22c55e"),
        ("Likely Authentic", report.likely_authentic, "#3b82f6"),
        ("Inconclusive", report.inconclusive, "#94a3b8"),
        ("Suspicious", report.suspicious, "#f59e0b"),
        ("Likely Artificial", report.likely_artificial, "#ef4444"),
    ]

    # Threat bar segments
    threat_segments = [
        (name, threat_counts.get(name, 0), _THREAT_COLORS.get(name, "#888"))
        for name in ["PFC Ghost Artist", "PFC + AI Hybrid", "Independent AI Artist",
                      "AI Fraud Farm", "AI Impersonation"]
    ]

    # Data sources panel
    sources_html = ""
    if report.api_source_counts:
        dots = []
        for name, count in report.api_source_counts.items():
            dots.append(f'<span class="src-dot"><span class="dot-ok"></span> {_esc(name)} ({count})</span>')
        sources_html = '<div class="sources-row">' + " ".join(dots) + '</div>'
        sources_html += f'<div style="color:#556;font-size:0.75rem;margin-top:4px">Total: {total_api_calls} API calls</div>'

    # Duration
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
  align-items: center;
  padding: 12px 16px;
  gap: 12px;
  cursor: pointer;
  user-select: none;
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
}}
.card-info {{ flex: 1; min-width: 0; }}
.card-name {{
  font-weight: 600;
  color: var(--text-bright);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.card-stats {{
  font-size: 0.78rem;
  color: var(--text-dim);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.pill {{
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 0.72rem;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
}}
.threat-pill {{
  font-size: 0.68rem;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
  flex-shrink: 0;
}}
.chevron {{
  color: var(--text-dim);
  font-size: 0.8rem;
  transition: transform 0.2s;
  flex-shrink: 0;
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
  <div class="subtitle">{_esc(report.playlist_name)} &middot; by {_esc(report.owner)} &middot; {now.strftime('%Y-%m-%d')}</div>
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
    <!-- Key metrics -->
    <div class="metric-row">
      <div class="metric-card">
        <div class="metric-value">{report.total_tracks}</div>
        <div class="metric-label">Tracks</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{report.total_unique_artists}</div>
        <div class="metric-label">Artists</div>
      </div>
      <div class="metric-card">
        <div class="metric-value" style="color:{('#ef4444' if contamination > 30 else '#f59e0b' if contamination > 10 else '#22c55e')}">{contamination:.0f}%</div>
        <div class="metric-label">Contamination</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{flagged}</div>
        <div class="metric-label">Flagged</div>
      </div>
      {f'<div class="metric-card"><div class="metric-value">{duration_str}</div><div class="metric-label">Scan Time</div></div>' if duration_str else ''}
      {f'<div class="metric-card"><div class="metric-value">{total_api_calls}</div><div class="metric-label">API Calls</div></div>' if total_api_calls else ''}
    </div>

    <!-- Verdict bar -->
    <div class="bar-section">
      <div class="bar-label">Verdict Breakdown</div>
      {_stacked_bar(verdict_segments, report.total_unique_artists)}
      <div class="legend">
        <span><span class="legend-dot" style="background:#22c55e"></span>Verified</span>
        <span><span class="legend-dot" style="background:#3b82f6"></span>Authentic</span>
        <span><span class="legend-dot" style="background:#94a3b8"></span>Inconclusive</span>
        <span><span class="legend-dot" style="background:#f59e0b"></span>Suspicious</span>
        <span><span class="legend-dot" style="background:#ef4444"></span>Artificial</span>
      </div>
    </div>

    <!-- Threat bar (only if flagged artists) -->
    {_threat_bar_section(threat_segments, flagged) if flagged else ''}

    <!-- Data sources -->
    {f'<div class="bar-section"><div class="bar-label">Data Sources</div>{sources_html}</div>' if sources_html else ''}
  </div>
</div>

<!-- Artist list -->
<div class="list-controls">
  <h2>Artist Analysis ({len(sorted_artists)})</h2>
  <button class="toggle-btn" onclick="toggleAll()">Expand All</button>
</div>

{"".join(artist_cards_html)}

<!-- Footer -->
<div class="footer">
  Generated by Playlist Authenticity Analyzer &middot; {now.strftime('%Y-%m-%d %H:%M UTC')}<br>
  {_esc(report.blocklist_version) if report.blocklist_version else ''}{' &middot; ' if report.blocklist_version else ''}
  {len(sorted_artists)} artists analyzed across {len([n for n, c in report.api_source_counts.items()]) if report.api_source_counts else 'multiple'} data sources
</div>

</div>
<script>
function toggleCard(el) {{
  el.closest('.card').classList.toggle('open');
}}
function toggleAll() {{
  const cards = document.querySelectorAll('.card');
  const btn = document.querySelector('.toggle-btn');
  const anyOpen = document.querySelector('.card.open');
  cards.forEach(c => {{
    if (anyOpen) c.classList.remove('open');
    else c.classList.add('open');
  }});
  btn.textContent = anyOpen ? 'Expand All' : 'Collapse All';
}}
// Auto-expand flagged artists
document.querySelectorAll('.card[data-flagged="true"]').forEach(c => c.classList.add('open'));
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


# ---------------------------------------------------------------------------
# Artist card builder
# ---------------------------------------------------------------------------

def _build_card(a: ArtistReport, ev: ArtistEvaluation | None, idx: int) -> str:
    """Build a complete artist card (collapsed + expandable detail)."""
    verdict_str = a.verdict
    score = a.final_score
    verdict_color = _VERDICT_COLORS.get(verdict_str, "#94a3b8")
    is_flagged = verdict_str in ("Suspicious", "Likely Artificial")

    # Score badge color
    if score >= 80:
        badge_bg = "#22c55e"
    elif score >= 55:
        badge_bg = "#3b82f6"
    elif score >= 35:
        badge_bg = "#94a3b8"
    elif score >= 18:
        badge_bg = "#f59e0b"
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

    return f"""<div class="card" data-flagged="{'true' if is_flagged else 'false'}" data-idx="{idx}">
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
    """Build the one-line stats summary for collapsed card."""
    if not ev:
        return ""
    ext = ev.external_data or ExternalData()
    parts: list[str] = []

    if ev.platform_presence.deezer_fans:
        parts.append(f"Deezer fans: {_fmt_num(ev.platform_presence.deezer_fans)}")
    if ext.lastfm_listeners:
        parts.append(f"Last.fm: {_fmt_num(ext.lastfm_listeners)} listeners")
    if ext.setlistfm_total_shows:
        parts.append(f"{ext.setlistfm_total_shows} shows")
    if ext.discogs_physical_releases:
        parts.append(f"{ext.discogs_physical_releases} vinyl/CD")
    if ext.wikipedia_found:
        parts.append("Wikipedia")

    # Fallback
    if not parts:
        parts.append(f"{len(ev.green_flags)} green / {len(ev.red_flags)} red flags")

    # Labels
    if ev.labels:
        parts.append(_esc(ev.labels[0]))

    return " &middot; ".join(parts[:4])


def _build_card_body(a: ArtistReport, ev: ArtistEvaluation) -> str:
    """Build the expanded card body with all detail sections."""
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

    # 2. Radar chart + signal bars
    radar = _radar_svg(scores, verdict_color)
    signal_bars = _build_signal_bars(scores)

    scorecard = f"""<div class="scorecard">
  <div>{radar}</div>
  <div>
    {signal_bars}
    {_build_sources_grid(ev, ext)}
  </div>
</div>"""

    # 3. Metadata grid
    meta = _build_metadata_grid(a, ev, ext)

    # 4. Evidence breakdown
    evidence = _build_evidence_section(ev)

    # 5. AI analysis (if available)
    ai_html = ""
    for sig in a.deep_signals:
        if isinstance(sig, dict) and sig.get("detail"):
            ai_html = (
                f'<div class="explanation" style="background:#1a2332;border-left:3px solid #a78bfa">'
                f'<div style="font-size:0.72rem;color:#a78bfa;text-transform:uppercase;margin-bottom:6px">'
                f'AI Analysis</div>{_esc(sig["detail"])}</div>'
            )
            break

    # 6. Related entities
    entities = _build_entities_section(ev, ext)

    return f"""
    {explanation_html}
    {scorecard}
    {meta}
    {evidence}
    {ai_html}
    {entities}
    """


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
        if val >= 60:
            color = "#22c55e"
        elif val >= 30:
            color = "#f59e0b"
        else:
            color = "#ef4444"
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
            icon = "&#9888;" if "confirmed_bad" in e.tags else "&#9888;"
            color = "#ef4444" if "confirmed_bad" in " ".join(e.tags) else "#f59e0b"
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
