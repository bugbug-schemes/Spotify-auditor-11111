"""
Report formatters: Markdown, HTML (with radar charts), and JSON output.

The HTML report produces:
- A summary table with all artists, sortable with click-to-expand
- Per-artist detailed scorecards with radar charts, evidence flags,
  data fields from all 10+ API sources, and match quality metadata
"""

from __future__ import annotations

import json
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

    lines.append(f"# Spotify Audit Report: {report.playlist_name}")
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
# HTML — Summary table + expandable artist detail cards
# ---------------------------------------------------------------------------

_VERDICT_COLORS_CSS = {
    "Verified Artist": "#22c55e",
    "Likely Authentic": "#84cc16",
    "Inconclusive": "#eab308",
    "Insufficient Data": "#a78bfa",
    "Conflicting Signals": "#f59e0b",
    "Suspicious": "#f97316",
    "Likely Artificial": "#ef4444",
}

_VERDICT_BG_CSS = {
    "Verified Artist": "#f0fdf4",
    "Likely Authentic": "#f7fee7",
    "Inconclusive": "#fefce8",
    "Insufficient Data": "#f5f3ff",
    "Conflicting Signals": "#fffbeb",
    "Suspicious": "#fff7ed",
    "Likely Artificial": "#fef2f2",
}


def _esc(text: str) -> str:
    """HTML-escape text."""
    return html_mod.escape(str(text))


def _fmt_num(n: int) -> str:
    """Format a number with commas."""
    return f"{n:,}"


def to_html(report: PlaylistReport) -> str:
    """Generate a self-contained HTML report with summary table + expandable cards."""
    artist_cards = []
    chart_scripts = []
    summary_rows = []

    for idx, a in enumerate(report.artists):
        ev = a.evaluation
        summary_rows.append(_build_summary_row(a, ev, idx))
        if ev:
            card_html, chart_js = _build_artist_card_html(a, ev, idx)
            artist_cards.append(card_html)
            chart_scripts.append(chart_js)

    health = report.health_score
    if health >= 80:
        health_color = "#22c55e"
    elif health >= 60:
        health_color = "#eab308"
    elif health >= 40:
        health_color = "#f97316"
    else:
        health_color = "#ef4444"

    summary_table = "\n".join(summary_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spotify Audit: {_esc(report.playlist_name)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    background: #0f0f0f; color: #e0e0e0; padding: 2rem;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{ color: #1DB954; font-size: 1.8rem; margin-bottom: 0.5rem; }}
  .subtitle {{ color: #888; margin-bottom: 2rem; }}

  /* Playlist header */
  .playlist-header {{
    background: #1a1a1a; border-radius: 12px; padding: 1.5rem; margin-bottom: 2rem;
    border: 1px solid #333; display: flex; align-items: center; gap: 2rem;
    flex-wrap: wrap;
  }}
  .health-block {{ text-align: center; min-width: 140px; }}
  .health-score {{
    font-size: 3rem; font-weight: 700;
  }}
  .health-label {{ color: #888; font-size: 0.85rem; margin-bottom: 0.3rem; }}
  .verdict-grid {{
    display: flex; gap: 0.5rem; flex-wrap: wrap; flex: 1;
    justify-content: center; align-items: center;
  }}
  .verdict-pill {{
    padding: 0.4rem 1rem; border-radius: 20px; font-size: 0.85rem;
    font-weight: 600; text-align: center; color: #111;
  }}
  .playlist-meta {{
    color: #888; font-size: 0.8rem; text-align: right; min-width: 160px;
  }}

  /* Summary table */
  .summary-section {{ margin-bottom: 2rem; }}
  .summary-section h2 {{
    color: #aaa; font-size: 0.85rem; text-transform: uppercase;
    letter-spacing: 0.08em; margin-bottom: 0.8rem;
  }}
  .summary-table {{
    width: 100%; border-collapse: collapse; background: #1a1a1a;
    border-radius: 12px; overflow: hidden; border: 1px solid #333;
  }}
  .summary-table th {{
    padding: 0.7rem 1rem; text-align: left; font-size: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.05em; color: #666;
    border-bottom: 1px solid #333; background: #151515;
    cursor: pointer; user-select: none; white-space: nowrap;
  }}
  .summary-table th:hover {{ color: #aaa; }}
  .summary-table th .sort-arrow {{ margin-left: 0.3rem; font-size: 0.7rem; }}
  .summary-table td {{
    padding: 0.6rem 1rem; font-size: 0.9rem; border-bottom: 1px solid #222;
  }}
  .summary-table tr {{ cursor: pointer; transition: background 0.15s; }}
  .summary-table tr:hover {{ background: #222; }}
  .summary-table tr.active {{ background: #1a2a1a; }}

  .verdict-dot {{
    display: inline-block; width: 10px; height: 10px; border-radius: 50%;
    margin-right: 0.5rem; vertical-align: middle;
  }}
  .verdict-text {{ font-size: 0.8rem; color: #bbb; }}
  .threat-tag {{
    font-size: 0.7rem; color: #f97316; background: #2a1a0a;
    padding: 0.15rem 0.5rem; border-radius: 10px;
  }}
  .score-cell {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  .platforms-cell {{ font-size: 0.8rem; color: #999; }}
  .key-stat {{ font-size: 0.8rem; color: #aaa; }}

  /* Detail section */
  .detail-section {{ margin-bottom: 1rem; }}
  .detail-section h2 {{
    color: #aaa; font-size: 0.85rem; text-transform: uppercase;
    letter-spacing: 0.08em; margin-bottom: 0.8rem;
  }}

  /* Artist cards */
  .artist-card {{
    background: #1a1a1a; border-radius: 12px; margin-bottom: 1rem;
    border: 1px solid #333; overflow: hidden;
  }}
  .artist-card.highlighted {{ border-color: #1DB954; }}
  .card-header {{
    padding: 1rem 1.5rem; display: flex; justify-content: space-between;
    align-items: center; cursor: pointer; border-bottom: 1px solid transparent;
    transition: background 0.15s;
  }}
  .card-header:hover {{ background: #222; }}
  .card-header.open {{ border-bottom-color: #333; }}
  .artist-name {{ font-size: 1.1rem; font-weight: 600; }}
  .header-badges {{ display: flex; align-items: center; gap: 0.5rem; }}
  .verdict-badge {{
    padding: 0.25rem 0.7rem; border-radius: 16px; font-size: 0.78rem;
    font-weight: 600; color: #111;
  }}
  .threat-badge {{
    background: #333; color: #f97316; padding: 0.25rem 0.7rem;
    border-radius: 16px; font-size: 0.72rem; font-weight: 600;
  }}
  .expand-icon {{
    color: #555; font-size: 1.2rem; transition: transform 0.2s;
    margin-left: 0.5rem;
  }}
  .card-header.open .expand-icon {{ transform: rotate(180deg); }}

  .card-body {{ padding: 1.5rem; display: none; }}
  .card-body.open {{ display: block; }}

  /* Two-column layout: chart + info */
  .scorecard-grid {{
    display: grid; grid-template-columns: 280px 1fr; gap: 2rem;
    align-items: start;
  }}
  @media (max-width: 768px) {{
    .scorecard-grid {{ grid-template-columns: 1fr; }}
  }}
  .chart-container {{ position: relative; width: 260px; height: 260px; }}

  /* Explanation */
  .explanation {{
    background: #222; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;
    font-size: 0.93rem; line-height: 1.5; border-left: 4px solid;
  }}

  /* Sources grid */
  .sources-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
    gap: 0.4rem; margin: 0.8rem 0;
  }}
  .source-item {{
    padding: 0.35rem 0.5rem; border-radius: 6px; font-size: 0.75rem;
    text-align: center; font-weight: 500;
  }}
  .source-ok {{ background: #052e16; color: #4ade80; border: 1px solid #166534; }}
  .source-miss {{ background: #1c1c1c; color: #555; border: 1px solid #333; }}
  .source-id {{ font-size: 0.65rem; color: #666; display: block; }}

  /* Signal category bars */
  .signal-bar-row {{
    display: flex; align-items: center; margin: 0.25rem 0; font-size: 0.82rem;
  }}
  .signal-label {{ width: 140px; color: #aaa; }}
  .signal-bar-bg {{
    flex: 1; height: 10px; background: #333; border-radius: 5px;
    overflow: hidden; margin: 0 0.6rem;
  }}
  .signal-bar-fill {{ height: 100%; border-radius: 5px; transition: width 0.3s; }}
  .signal-score {{ width: 40px; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }}

  /* Flags */
  .section-title {{
    font-size: 0.82rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.05em; margin: 1.2rem 0 0.5rem 0; padding-bottom: 0.3rem;
    border-bottom: 1px solid #333;
  }}
  .flag-item {{ padding: 0.4rem 0; border-bottom: 1px solid #222; }}
  .flag-finding {{ font-weight: 500; font-size: 0.9rem; }}
  .flag-detail {{ color: #888; font-size: 0.82rem; margin-top: 0.15rem; line-height: 1.4; }}
  .flag-meta {{ color: #666; font-size: 0.72rem; margin-top: 0.1rem; }}
  .strength-strong {{ color: #ef4444; }}
  .strength-moderate {{ color: #f97316; }}
  .strength-weak {{ color: #eab308; }}
  .green .strength-strong {{ color: #22c55e; }}
  .green .strength-moderate {{ color: #84cc16; }}
  .green .strength-weak {{ color: #a3e635; }}

  /* Data fields — organized by platform */
  .platform-data {{
    margin: 0.8rem 0; padding: 0.8rem 1rem;
    background: #151515; border-radius: 8px; border: 1px solid #2a2a2a;
  }}
  .platform-data-header {{
    font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.04em; color: #888; margin-bottom: 0.5rem;
    display: flex; align-items: center; gap: 0.5rem;
  }}
  .platform-data-header .match-info {{
    font-weight: 400; font-size: 0.7rem; color: #555;
    text-transform: none; letter-spacing: 0;
  }}
  .data-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.3rem;
  }}
  .data-field {{
    font-size: 0.78rem; padding: 0.2rem 0.4rem;
    color: #bbb;
  }}
  .data-field strong {{ color: #ddd; }}

  /* Footer */
  .footer {{
    text-align: center; color: #444; margin-top: 2rem;
    font-size: 0.78rem; padding: 1rem;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>Spotify Audit Report</h1>
  <p class="subtitle">{_esc(report.playlist_name)}</p>

  <div class="playlist-header">
    <div class="health-block">
      <div class="health-label">Health Score</div>
      <div class="health-score" style="color: {health_color}">{health}<span style="font-size: 1.2rem; color: #666">/100</span></div>
    </div>
    <div class="verdict-grid">
      <span class="verdict-pill" style="background: #22c55e">Verified: {report.verified_artists}</span>
      <span class="verdict-pill" style="background: #84cc16">Authentic: {report.likely_authentic}</span>
      <span class="verdict-pill" style="background: #eab308">Inconclusive: {report.inconclusive}</span>
      <span class="verdict-pill" style="background: #f97316">Suspicious: {report.suspicious}</span>
      <span class="verdict-pill" style="background: #ef4444; color: #fff">Artificial: {report.likely_artificial}</span>
    </div>
    <div class="playlist-meta">
      {_esc(report.owner)}<br>
      {report.total_tracks} tracks<br>
      {report.total_unique_artists} artists
    </div>
  </div>

  <div class="summary-section">
    <h2>Artist Summary</h2>
    <table class="summary-table" id="summaryTable">
      <thead>
        <tr>
          <th data-sort="name">Artist <span class="sort-arrow"></span></th>
          <th data-sort="verdict">Verdict <span class="sort-arrow"></span></th>
          <th data-sort="score">Score <span class="sort-arrow"></span></th>
          <th data-sort="threat">Threat <span class="sort-arrow"></span></th>
          <th data-sort="platforms">Platforms <span class="sort-arrow"></span></th>
          <th data-sort="key">Key Stats</th>
        </tr>
      </thead>
      <tbody>
        {summary_table}
      </tbody>
    </table>
  </div>

  <div class="detail-section" id="detailSection">
    <h2>Detailed Evidence</h2>
    {"".join(artist_cards)}
  </div>

</div>

<script>
// Toggle card open/close
document.querySelectorAll('.card-header').forEach(h => {{
  h.addEventListener('click', () => {{
    const body = h.nextElementSibling;
    body.classList.toggle('open');
    h.classList.toggle('open');
  }});
}});

// Summary row click → scroll to and expand artist card
document.querySelectorAll('.summary-table tbody tr').forEach(row => {{
  row.addEventListener('click', () => {{
    const idx = row.dataset.idx;
    const card = document.getElementById('artist-' + idx);
    if (!card) return;

    // Highlight row
    document.querySelectorAll('.summary-table tr.active').forEach(r => r.classList.remove('active'));
    row.classList.add('active');

    // Open card and scroll
    const body = card.querySelector('.card-body');
    const header = card.querySelector('.card-header');
    if (body && !body.classList.contains('open')) {{
      body.classList.add('open');
      header.classList.add('open');
    }}
    card.classList.add('highlighted');
    card.scrollIntoView({{ behavior: 'smooth', block: 'start' }});

    // Remove highlight after animation
    setTimeout(() => card.classList.remove('highlighted'), 2000);
  }});
}});

// Column sorting
(function() {{
  const table = document.getElementById('summaryTable');
  if (!table) return;
  const headers = table.querySelectorAll('th[data-sort]');
  let currentSort = null;
  let ascending = true;

  headers.forEach(th => {{
    th.addEventListener('click', () => {{
      const col = th.dataset.sort;
      if (currentSort === col) {{
        ascending = !ascending;
      }} else {{
        currentSort = col;
        ascending = true;
      }}

      const tbody = table.querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));

      rows.sort((a, b) => {{
        let va = a.dataset[col] || '';
        let vb = b.dataset[col] || '';
        // Try numeric
        const na = parseFloat(va);
        const nb = parseFloat(vb);
        if (!isNaN(na) && !isNaN(nb)) {{
          return ascending ? na - nb : nb - na;
        }}
        return ascending ? va.localeCompare(vb) : vb.localeCompare(va);
      }});

      rows.forEach(r => tbody.appendChild(r));

      // Update sort arrows
      headers.forEach(h => {{
        h.querySelector('.sort-arrow').textContent = '';
      }});
      th.querySelector('.sort-arrow').textContent = ascending ? '\\u25B2' : '\\u25BC';
    }});
  }});
}})();

// Render radar charts — lazy, only when card opens
const chartRendered = {{}};
function renderChart(idx) {{
  if (chartRendered[idx]) return;
  chartRendered[idx] = true;
  const el = document.getElementById('chart_' + idx);
  if (!el || !window._chartConfigs || !window._chartConfigs[idx]) return;
  const cfg = window._chartConfigs[idx];
  new Chart(el, cfg);
}}

// Observe card open to render charts lazily
const observer = new MutationObserver(mutations => {{
  mutations.forEach(m => {{
    if (m.target.classList.contains('open')) {{
      const canvas = m.target.querySelector('canvas');
      if (canvas) {{
        const idx = canvas.id.replace('chart_', '');
        renderChart(parseInt(idx));
      }}
    }}
  }});
}});
document.querySelectorAll('.card-body').forEach(body => {{
  observer.observe(body, {{ attributes: true, attributeFilter: ['class'] }});
}});

// Chart configs
window._chartConfigs = {{}};
{"".join(chart_scripts)}
</script>

<div class="footer">
  Report generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &mdash;
  Data from {_count_sources(report)} sources
</div>
</body>
</html>"""


def _count_sources(report: PlaylistReport) -> int:
    """Count unique data sources used across all artists."""
    sources: set[str] = set()
    for a in report.artists:
        if a.evaluation and a.evaluation.sources_reached:
            for name, reached in a.evaluation.sources_reached.items():
                if reached:
                    sources.add(name)
    return len(sources) if sources else 0


def _build_summary_row(a: ArtistReport, ev: ArtistEvaluation | None, idx: int) -> str:
    """Build a single <tr> for the summary table."""
    verdict_str = ev.verdict.value if ev else a.label
    verdict_color = _VERDICT_COLORS_CSS.get(verdict_str, "#888")
    threat = a.threat_category_name or ""
    score = a.final_score

    if score >= 80:
        score_color = "#22c55e"
    elif score >= 55:
        score_color = "#84cc16"
    elif score >= 35:
        score_color = "#eab308"
    elif score >= 15:
        score_color = "#f97316"
    else:
        score_color = "#ef4444"

    # Platforms count
    platforms = ev.platform_presence.count() if ev else 0

    # Key stat: pick the most interesting metric
    key_stat = _build_key_stat(ev)

    threat_html = f'<span class="threat-tag">{_esc(threat)}</span>' if threat else ""

    # Verdict sort order for table sorting
    verdict_order = {"Verified Artist": 0, "Likely Authentic": 1, "Inconclusive": 2,
                     "Insufficient Data": 2, "Conflicting Signals": 2,
                     "Suspicious": 3, "Likely Artificial": 4}.get(verdict_str, 2)

    return f"""<tr data-idx="{idx}" data-name="{_esc(a.artist_name)}" data-verdict="{verdict_order}" data-score="{score}" data-threat="{_esc(threat)}" data-platforms="{platforms}">
  <td><strong>{_esc(a.artist_name)}</strong></td>
  <td><span class="verdict-dot" style="background: {verdict_color}"></span><span class="verdict-text">{_esc(verdict_str)}</span></td>
  <td class="score-cell" style="color: {score_color}">{score}</td>
  <td>{threat_html}</td>
  <td class="platforms-cell">{platforms} APIs</td>
  <td class="key-stat">{key_stat}</td>
</tr>"""


def _build_key_stat(ev: ArtistEvaluation | None) -> str:
    """Pick the most interesting stat for the summary table."""
    if not ev:
        return ""
    ext = ev.external_data or ExternalData()
    parts: list[str] = []

    if ev.platform_presence.deezer_fans and ev.platform_presence.deezer_fans > 0:
        parts.append(f"{_fmt_num(ev.platform_presence.deezer_fans)} fans")
    if ext.lastfm_listeners:
        parts.append(f"{_fmt_num(ext.lastfm_listeners)} listeners")
    if ext.setlistfm_total_shows:
        parts.append(f"{ext.setlistfm_total_shows} shows")
    if ext.songkick_total_past_events:
        parts.append(f"{ext.songkick_total_past_events} concerts")
    if ext.wikipedia_found:
        parts.append("Wikipedia")
    if ext.youtube_channel_found:
        parts.append(f"{_fmt_num(ext.youtube_subscriber_count)} subs")
    if ext.discogs_physical_releases:
        parts.append(f"{ext.discogs_physical_releases} vinyl/CD")

    if not parts:
        red = len(ev.red_flags)
        green = len(ev.green_flags)
        return f"{green}G / {red}R flags"

    return ", ".join(parts[:3])


def _build_artist_card_html(a: ArtistReport, ev: ArtistEvaluation, idx: int) -> tuple[str, str]:
    """Build HTML card + Chart.js config for one artist."""
    verdict_str = ev.verdict.value
    verdict_color = _VERDICT_COLORS_CSS.get(verdict_str, "#888")
    verdict_bg = _VERDICT_BG_CSS.get(verdict_str, "#1a1a1a")
    scores = ev.category_scores
    sources = ev.sources_reached
    ext = ev.external_data or ExternalData()

    # Threat category badge
    threat_html = ""
    if a.threat_category_name:
        threat_html = f'<span class="threat-badge">{_esc(a.threat_category_name)}</span>'

    # Plain-English explanation
    explanation = _build_explanation(ev)

    # Source status with match quality annotation
    source_html = ""
    for name, reached in sources.items():
        cls = "source-ok" if reached else "source-miss"
        match_note = ""
        platform_key = name.lower().replace(".", "").replace(" ", "")
        # Map source display names to match_confidences keys
        key_map = {"spotify": "spotify", "deezer": "deezer", "genius": "genius",
                   "discogs": "discogs", "setlistfm": "setlistfm", "musicbrainz": "musicbrainz",
                   "lastfm": "lastfm", "last.fm": "lastfm", "wikipedia": "wikipedia",
                   "songkick": "songkick", "youtube": "youtube"}
        mk = key_map.get(platform_key, platform_key)
        if ext.match_methods.get(mk):
            method = ext.match_methods[mk]
            conf = ext.match_confidences.get(mk, 0)
            if method == "platform_id":
                match_note = '<span class="source-id">via ID</span>'
            elif conf > 0:
                match_note = f'<span class="source-id">{method} ({conf:.0%})</span>'
        source_html += f'<span class="{cls}">{_esc(name)}{match_note}</span>\n'

    # Signal bars
    bars_html = ""
    for cat, score in scores.items():
        if score >= 60:
            fill_color = "#22c55e"
        elif score >= 30:
            fill_color = "#eab308"
        else:
            fill_color = "#ef4444"
        bars_html += f"""<div class="signal-bar-row">
  <span class="signal-label">{_esc(cat)}</span>
  <div class="signal-bar-bg"><div class="signal-bar-fill" style="width: {score}%; background: {fill_color}"></div></div>
  <span class="signal-score">{score}</span>
</div>\n"""

    # Data fields organized by platform
    data_fields_html = _build_data_fields_html(ev, ext)

    # Red flags
    red_html = ""
    if ev.red_flags:
        red_html += f'<div class="section-title" style="color: #ef4444">Red Flags ({len(ev.red_flags)})</div>\n'
        for e in ev.red_flags:
            strength_cls = f"strength-{e.strength}"
            red_html += f"""<div class="flag-item">
  <div class="flag-finding"><span class="{strength_cls}">[{_esc(e.strength.upper())}]</span> {_esc(e.finding)}</div>
  <div class="flag-detail">{_esc(e.detail)}</div>
  <div class="flag-meta">Source: {_esc(e.source)}</div>
</div>\n"""

    # Green flags
    green_html = ""
    if ev.green_flags:
        green_html += f'<div class="section-title green" style="color: #22c55e">Green Flags ({len(ev.green_flags)})</div>\n'
        for e in ev.green_flags:
            strength_cls = f"strength-{e.strength}"
            green_html += f"""<div class="flag-item green">
  <div class="flag-finding"><span class="{strength_cls}">[{_esc(e.strength.upper())}]</span> {_esc(e.finding)}</div>
  <div class="flag-detail">{_esc(e.detail)}</div>
  <div class="flag-meta">Source: {_esc(e.source)}</div>
</div>\n"""

    # Neutral
    neutral_html = ""
    if ev.neutral_notes:
        neutral_html += '<div class="section-title" style="color: #888">Notes</div>\n'
        for e in ev.neutral_notes:
            neutral_html += f'<div class="flag-item"><div class="flag-finding">{_esc(e.finding)} <span class="flag-meta">({_esc(e.source)})</span></div></div>\n'

    canvas_id = f"chart_{idx}"

    card_html = f"""<div class="artist-card" id="artist-{idx}">
  <div class="card-header">
    <span class="artist-name">{_esc(a.artist_name)}</span>
    <div class="header-badges">
      <span class="verdict-badge" style="background: {verdict_color}">{_esc(verdict_str)}</span>
      {threat_html}
      <span class="expand-icon">&#9660;</span>
    </div>
  </div>
  <div class="card-body">
    <div class="explanation" style="border-color: {verdict_color}; background: {verdict_bg}20">
      <span style="color: #e0e0e0">{explanation}</span>
    </div>

    <div class="scorecard-grid">
      <div>
        <div class="chart-container">
          <canvas id="{canvas_id}"></canvas>
        </div>
      </div>
      <div>
        <div class="section-title" style="color: #aaa">Signal Scores</div>
        {bars_html}

        <div class="section-title" style="color: #aaa">Data Sources</div>
        <div class="sources-grid">
          {source_html}
        </div>
      </div>
    </div>

    {data_fields_html}
    {red_html}
    {green_html}
    {neutral_html}
  </div>
</div>\n"""

    # Chart.js config — stored for lazy rendering
    labels = list(scores.keys())
    values = list(scores.values())
    chart_js = f"""
window._chartConfigs[{idx}] = {{
  type: 'radar',
  data: {{
    labels: {json.dumps(labels)},
    datasets: [{{
      label: '{_esc(a.artist_name)}',
      data: {json.dumps(values)},
      backgroundColor: '{verdict_color}33',
      borderColor: '{verdict_color}',
      borderWidth: 2,
      pointBackgroundColor: '{verdict_color}',
      pointBorderColor: '#fff',
      pointRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: true,
    scales: {{
      r: {{
        beginAtZero: true,
        max: 100,
        ticks: {{ display: false }},
        grid: {{ color: '#333' }},
        angleLines: {{ color: '#333' }},
        pointLabels: {{
          color: '#aaa',
          font: {{ size: 10 }},
        }},
      }}
    }},
    plugins: {{
      legend: {{ display: false }},
    }},
  }}
}};
"""

    return card_html, chart_js


def _build_explanation(ev: ArtistEvaluation) -> str:
    """Generate a plain-English explanation of the verdict."""
    name = ev.artist_name
    verdict = ev.verdict
    platforms = ev.platform_presence.count()
    fans = ev.platform_presence.deezer_fans
    red_count = len(ev.red_flags)
    green_count = len(ev.green_flags)
    strong_reds = len(ev.strong_red_flags)
    strong_greens = len(ev.strong_green_flags)

    if verdict == Verdict.VERIFIED_ARTIST:
        parts = [f"{name} looks like a real, established artist."]
        if platforms >= 5:
            parts.append(f"They were found on {platforms} different music platforms.")
        if fans >= 100_000:
            parts.append(f"They have {fans:,} fans on Deezer.")
        if green_count >= 10:
            parts.append(f"We found {green_count} positive signals and no serious concerns.")
        return " ".join(parts)

    elif verdict == Verdict.LIKELY_AUTHENTIC:
        parts = [f"{name} appears to be a legitimate artist."]
        if platforms >= 3:
            parts.append(f"Found on {platforms} platforms with mostly positive signals.")
        parts.append(f"We found {green_count} green flags and {red_count} red flags.")
        return " ".join(parts)

    elif verdict == Verdict.INSUFFICIENT_DATA:
        parts = [f"We don't have enough data to evaluate {name}."]
        total = green_count + red_count
        parts.append(f"Only {total} signal{'s' if total != 1 else ''} collected — too few to draw a conclusion.")
        parts.append("This often happens with brand-new or very niche artists who haven't built an online footprint yet.")
        return " ".join(parts)

    elif verdict == Verdict.CONFLICTING_SIGNALS:
        parts = [f"The evidence on {name} is contradictory."]
        parts.append(f"We found {green_count} positive and {red_count} negative signals, both substantial.")
        parts.append("This can happen with real artists on PFC-associated labels, or legitimate acts with unusual release patterns.")
        return " ".join(parts)

    elif verdict == Verdict.INCONCLUSIVE:
        parts = [f"We couldn't make a confident determination about {name}."]
        parts.append(f"The evidence is mixed: {green_count} positive and {red_count} negative signals.")
        parts.append("This could be a new artist, a niche act, or something worth investigating further.")
        return " ".join(parts)

    elif verdict == Verdict.SUSPICIOUS:
        parts = [f"{name} shows several warning signs."]
        if strong_reds:
            parts.append(f"We found {strong_reds} strong red flag{'s' if strong_reds != 1 else ''}.")
        if platforms <= 2:
            parts.append(f"Only found on {platforms} platform{'s' if platforms != 1 else ''}.")
        parts.append("This doesn't prove they're fake, but the pattern is worth scrutiny.")
        return " ".join(parts)

    elif verdict == Verdict.LIKELY_ARTIFICIAL:
        parts = [f"{name} has strong indicators of being an artificial or manufactured artist."]
        if strong_reds >= 3:
            parts.append(f"We found {strong_reds} strong red flags.")
        for e in ev.red_flags:
            if e.tags and {"pfc_label", "content_farm"} & set(e.tags):
                parts.append("The release pattern and distributor match known content farm operations.")
                break
        return " ".join(parts)

    return f"Evaluated {name}: {green_count} green flags, {red_count} red flags."


def _build_data_fields_html(ev: ArtistEvaluation, ext: ExternalData) -> str:
    """Build data fields organized by platform source."""
    sections: list[str] = []

    # --- Spotify / Deezer (always available) ---
    spotify_fields: list[str] = []
    if ev.platform_presence.deezer_fans:
        spotify_fields.append(f"<strong>Deezer fans:</strong> {_fmt_num(ev.platform_presence.deezer_fans)}")
    if ext.deezer_ai_checked:
        if ext.deezer_ai_tagged_albums:
            spotify_fields.append(f"<strong>AI tagged:</strong> {', '.join(_esc(a) for a in ext.deezer_ai_tagged_albums[:3])}")
        else:
            spotify_fields.append("<strong>AI tagged:</strong> none detected")
    if spotify_fields:
        sections.append(_platform_section("Spotify / Deezer", spotify_fields, ext, "deezer"))

    # --- Genius ---
    genius_fields: list[str] = []
    if ext.genius_found:
        if ext.genius_song_count:
            genius_fields.append(f"<strong>Songs:</strong> {ext.genius_song_count}")
        if ext.genius_is_verified:
            genius_fields.append("<strong>Status:</strong> Verified")
        if ext.genius_followers_count:
            genius_fields.append(f"<strong>Followers:</strong> {_fmt_num(ext.genius_followers_count)}")
        if ext.genius_facebook_name:
            genius_fields.append(f"<strong>Facebook:</strong> {_esc(ext.genius_facebook_name)}")
        if ext.genius_instagram_name:
            genius_fields.append(f"<strong>Instagram:</strong> {_esc(ext.genius_instagram_name)}")
        if ext.genius_twitter_name:
            genius_fields.append(f"<strong>Twitter/X:</strong> {_esc(ext.genius_twitter_name)}")
        if ext.genius_alternate_names:
            genius_fields.append(f"<strong>Also known as:</strong> {_esc(', '.join(ext.genius_alternate_names[:3]))}")
    if genius_fields:
        sections.append(_platform_section("Genius", genius_fields, ext, "genius"))

    # --- MusicBrainz ---
    mb_fields: list[str] = []
    if ext.musicbrainz_found:
        if ext.musicbrainz_type:
            mb_fields.append(f"<strong>Type:</strong> {_esc(ext.musicbrainz_type)}")
        if ext.musicbrainz_gender:
            mb_fields.append(f"<strong>Gender:</strong> {_esc(ext.musicbrainz_gender)}")
        if ext.musicbrainz_country:
            mb_fields.append(f"<strong>Country:</strong> {_esc(ext.musicbrainz_country)}")
        if ext.musicbrainz_area:
            mb_fields.append(f"<strong>Area:</strong> {_esc(ext.musicbrainz_area)}")
        if ext.musicbrainz_begin_date:
            mb_fields.append(f"<strong>Active since:</strong> {_esc(ext.musicbrainz_begin_date)}")
        if ext.musicbrainz_genres:
            mb_fields.append(f"<strong>Genres:</strong> {_esc(', '.join(ext.musicbrainz_genres[:5]))}")
        if ext.musicbrainz_isnis:
            mb_fields.append(f"<strong>ISNI:</strong> {_esc(ext.musicbrainz_isnis[0])}")
        if ext.musicbrainz_ipis:
            mb_fields.append(f"<strong>IPI:</strong> {_esc(ext.musicbrainz_ipis[0])}")
        if ext.musicbrainz_aliases:
            mb_fields.append(f"<strong>Aliases:</strong> {_esc(', '.join(ext.musicbrainz_aliases[:3]))}")
        # Social / web links from MusicBrainz
        social_links: list[str] = []
        if ext.musicbrainz_official_website:
            social_links.append(f'<a href="{_esc(ext.musicbrainz_official_website)}" target="_blank" style="color:#1DB954">Website</a>')
        if ext.musicbrainz_youtube_url:
            social_links.append(f'<a href="{_esc(ext.musicbrainz_youtube_url)}" target="_blank" style="color:#1DB954">YouTube</a>')
        if ext.musicbrainz_bandcamp_url:
            social_links.append(f'<a href="{_esc(ext.musicbrainz_bandcamp_url)}" target="_blank" style="color:#1DB954">Bandcamp</a>')
        for rt, url in list(ext.musicbrainz_social_urls.items())[:4]:
            social_links.append(f'<a href="{_esc(url)}" target="_blank" style="color:#1DB954">{_esc(rt)}</a>')
        if social_links:
            mb_fields.append(f"<strong>Links:</strong> {' &middot; '.join(social_links)}")
    if mb_fields:
        sections.append(_platform_section("MusicBrainz", mb_fields, ext, "musicbrainz"))

    # --- Discogs ---
    discogs_fields: list[str] = []
    if ext.discogs_found:
        if ext.discogs_physical_releases:
            discogs_fields.append(f"<strong>Physical releases:</strong> {ext.discogs_physical_releases}")
        if ext.discogs_digital_releases:
            discogs_fields.append(f"<strong>Digital releases:</strong> {ext.discogs_digital_releases}")
        if ext.discogs_total_releases:
            discogs_fields.append(f"<strong>Total releases:</strong> {ext.discogs_total_releases}")
        if ext.discogs_formats:
            discogs_fields.append(f"<strong>Formats:</strong> {_esc(', '.join(ext.discogs_formats[:5]))}")
        if ext.discogs_labels:
            discogs_fields.append(f"<strong>Labels:</strong> {_esc(', '.join(ext.discogs_labels[:4]))}")
        if ext.discogs_realname:
            discogs_fields.append(f"<strong>Real name:</strong> {_esc(ext.discogs_realname)}")
        if ext.discogs_members:
            discogs_fields.append(f"<strong>Members:</strong> {_esc(', '.join(ext.discogs_members[:4]))}")
        if ext.discogs_groups:
            discogs_fields.append(f"<strong>Groups:</strong> {_esc(', '.join(ext.discogs_groups[:3]))}")
        if ext.discogs_profile:
            bio_preview = ext.discogs_profile[:120].rstrip()
            if len(ext.discogs_profile) > 120:
                bio_preview += "..."
            discogs_fields.append(f"<strong>Bio:</strong> {_esc(bio_preview)}")
    if discogs_fields:
        sections.append(_platform_section("Discogs", discogs_fields, ext, "discogs"))

    # --- Last.fm ---
    lastfm_fields: list[str] = []
    if ext.lastfm_found:
        if ext.lastfm_listeners:
            lastfm_fields.append(f"<strong>Listeners:</strong> {_fmt_num(ext.lastfm_listeners)}")
        if ext.lastfm_playcount:
            lastfm_fields.append(f"<strong>Play count:</strong> {_fmt_num(ext.lastfm_playcount)}")
        if ext.lastfm_listener_play_ratio:
            lastfm_fields.append(f"<strong>Play/listener ratio:</strong> {ext.lastfm_listener_play_ratio:.1f}")
        if ext.lastfm_tags:
            lastfm_fields.append(f"<strong>Tags:</strong> {_esc(', '.join(ext.lastfm_tags[:5]))}")
        if ext.lastfm_similar_artists:
            lastfm_fields.append(f"<strong>Similar to:</strong> {_esc(', '.join(ext.lastfm_similar_artists[:4]))}")
        if ext.lastfm_bio_exists:
            lastfm_fields.append("<strong>Bio:</strong> exists")
    if lastfm_fields:
        sections.append(_platform_section("Last.fm", lastfm_fields, ext, "lastfm"))

    # --- Wikipedia ---
    wiki_fields: list[str] = []
    if ext.wikipedia_found:
        if ext.wikipedia_title:
            wiki_fields.append(f"<strong>Article:</strong> {_esc(ext.wikipedia_title)}")
        if ext.wikipedia_monthly_views:
            wiki_fields.append(f"<strong>Monthly views:</strong> {_fmt_num(ext.wikipedia_monthly_views)}")
        if ext.wikipedia_length:
            wiki_fields.append(f"<strong>Article length:</strong> {_fmt_num(ext.wikipedia_length)} bytes")
        if ext.wikipedia_description:
            wiki_fields.append(f"<strong>Description:</strong> {_esc(ext.wikipedia_description)}")
        if ext.wikipedia_categories:
            wiki_fields.append(f"<strong>Categories:</strong> {_esc(', '.join(ext.wikipedia_categories[:4]))}")
        if ext.wikipedia_url:
            wiki_fields.append(f'<strong>Link:</strong> <a href="{_esc(ext.wikipedia_url)}" target="_blank" style="color:#1DB954">Wikipedia</a>')
    if wiki_fields:
        sections.append(_platform_section("Wikipedia", wiki_fields, ext, "wikipedia"))

    # --- Setlist.fm + Songkick (concerts) ---
    concert_fields: list[str] = []
    if ext.setlistfm_found:
        if ext.setlistfm_total_shows:
            concert_fields.append(f"<strong>Setlist.fm shows:</strong> {ext.setlistfm_total_shows}")
        if ext.setlistfm_first_show:
            concert_fields.append(f"<strong>First show:</strong> {_esc(ext.setlistfm_first_show)}")
        if ext.setlistfm_last_show:
            concert_fields.append(f"<strong>Last show:</strong> {_esc(ext.setlistfm_last_show)}")
        if ext.setlistfm_tour_names:
            concert_fields.append(f"<strong>Tours:</strong> {_esc(', '.join(ext.setlistfm_tour_names[:3]))}")
        if ext.setlistfm_venue_countries:
            concert_fields.append(f"<strong>Countries:</strong> {_esc(', '.join(ext.setlistfm_venue_countries[:6]))}")
    if ext.songkick_found:
        if ext.songkick_total_past_events:
            concert_fields.append(f"<strong>Songkick events:</strong> {ext.songkick_total_past_events}")
        if ext.songkick_total_upcoming_events:
            concert_fields.append(f"<strong>Upcoming:</strong> {ext.songkick_total_upcoming_events}")
        if ext.songkick_on_tour:
            concert_fields.append("<strong>Status:</strong> Currently on tour")
        if ext.songkick_first_event_date:
            concert_fields.append(f"<strong>First event:</strong> {_esc(ext.songkick_first_event_date)}")
        if ext.songkick_last_event_date:
            concert_fields.append(f"<strong>Last event:</strong> {_esc(ext.songkick_last_event_date)}")
        if ext.songkick_venue_countries:
            sk_countries = [c for c in ext.songkick_venue_countries if c not in (ext.setlistfm_venue_countries or [])]
            if sk_countries:
                concert_fields.append(f"<strong>Songkick countries:</strong> {_esc(', '.join(sk_countries[:5]))}")
    if concert_fields:
        match_key = "setlistfm" if ext.setlistfm_found else "songkick"
        sections.append(_platform_section("Concerts / Touring", concert_fields, ext, match_key))

    # --- YouTube ---
    yt_fields: list[str] = []
    if ext.youtube_checked and ext.youtube_channel_found:
        if ext.youtube_subscriber_count:
            yt_fields.append(f"<strong>Subscribers:</strong> {_fmt_num(ext.youtube_subscriber_count)}")
        if ext.youtube_video_count:
            yt_fields.append(f"<strong>Videos:</strong> {_fmt_num(ext.youtube_video_count)}")
        if ext.youtube_view_count:
            yt_fields.append(f"<strong>Total views:</strong> {_fmt_num(ext.youtube_view_count)}")
        if ext.youtube_music_videos_found:
            yt_fields.append(f"<strong>Music videos:</strong> {ext.youtube_music_videos_found}")
    if yt_fields:
        sections.append(_platform_section("YouTube", yt_fields, ext, "youtube"))

    # --- PRO Registry ---
    pro_fields: list[str] = []
    if ext.pro_checked:
        registries: list[str] = []
        if ext.pro_found_bmi:
            registries.append("BMI")
        if ext.pro_found_ascap:
            registries.append("ASCAP")
        if registries:
            pro_fields.append(f"<strong>Registered:</strong> {', '.join(registries)}")
        elif ext.pro_checked:
            pro_fields.append("<strong>Registered:</strong> not found in BMI/ASCAP")
        if ext.pro_works_count:
            pro_fields.append(f"<strong>Registered works:</strong> {ext.pro_works_count}")
        if ext.pro_publishers:
            pro_fields.append(f"<strong>Publishers:</strong> {_esc(', '.join(ext.pro_publishers[:3]))}")
        if ext.pro_pfc_publisher_match:
            pro_fields.append("<strong>PFC publisher:</strong> match found")
        if ext.pro_songwriter_registered:
            pro_fields.append("<strong>Songwriter:</strong> registered")
    if pro_fields:
        sections.append(_platform_section("PRO Registry", pro_fields, ext, None))

    # --- Press Coverage ---
    press_fields: list[str] = []
    if ext.press_checked:
        if ext.press_publications_found:
            press_fields.append(f"<strong>Publications:</strong> {_esc(', '.join(ext.press_publications_found[:5]))}")
        if ext.press_total_hits:
            press_fields.append(f"<strong>Total coverage:</strong> {ext.press_total_hits} hits")
        if not ext.press_publications_found and not ext.press_total_hits:
            press_fields.append("<strong>Coverage:</strong> none found")
    if press_fields:
        sections.append(_platform_section("Press Coverage", press_fields, ext, None))

    # --- ISRC ---
    isrc_fields: list[str] = []
    if ext.isrcs:
        if ext.isrc_registrants:
            isrc_fields.append(f"<strong>Registrants:</strong> {_esc(', '.join(ext.isrc_registrants[:4]))}")
        isrc_fields.append(f"<strong>ISRC codes:</strong> {len(ext.isrcs)} tracked")
    if isrc_fields:
        sections.append(_platform_section("ISRC Data", isrc_fields, ext, None))

    if not sections:
        return ""

    return f'<div class="section-title" style="color: #aaa">Platform Data</div>\n' + "\n".join(sections)


def _platform_section(title: str, fields: list[str], ext: ExternalData, match_key: str | None) -> str:
    """Wrap data fields in a platform-labeled section with optional match quality info."""
    match_info = ""
    if match_key and ext.match_methods.get(match_key):
        method = ext.match_methods[match_key]
        conf = ext.match_confidences.get(match_key, 0)
        had_id = ext.had_platform_ids.get(match_key, False)
        if method == "platform_id" or had_id:
            match_info = '<span class="match-info">matched via platform ID</span>'
        elif conf > 0:
            match_info = f'<span class="match-info">matched via {method} ({conf:.0%})</span>'

    items = "\n".join(f'<span class="data-field">{f}</span>' for f in fields)
    return f"""<div class="platform-data">
  <div class="platform-data-header">{_esc(title)} {match_info}</div>
  <div class="data-grid">{items}</div>
</div>"""
