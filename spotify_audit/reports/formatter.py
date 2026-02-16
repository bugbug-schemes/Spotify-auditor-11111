"""
Report formatters: Markdown, HTML (with radar charts), and JSON output.

The HTML report produces per-artist scorecards with:
- Verdict + radar chart showing 6 signal category scores
- Plain-English explanation
- API source status (which APIs were reached)
- Detailed signal breakdown (red/green flags with explanations)
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

    # Legacy score data (supplementary)
    if a.quick_score is not None:
        d["legacy_quick_score"] = a.quick_score
    if a.standard_score is not None:
        d["legacy_standard_score"] = a.standard_score
    if a.quick_signals:
        d["quick_signals"] = a.quick_signals

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
                bar = "█" * (score // 5) + "░" * (20 - score // 5)
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
# HTML — Full scorecard with Chart.js radar charts
# ---------------------------------------------------------------------------

_VERDICT_COLORS_CSS = {
    "Verified Artist": "#22c55e",
    "Likely Authentic": "#84cc16",
    "Inconclusive": "#eab308",
    "Suspicious": "#f97316",
    "Likely Artificial": "#ef4444",
}

_VERDICT_BG_CSS = {
    "Verified Artist": "#f0fdf4",
    "Likely Authentic": "#f7fee7",
    "Inconclusive": "#fefce8",
    "Suspicious": "#fff7ed",
    "Likely Artificial": "#fef2f2",
}


def _esc(text: str) -> str:
    """HTML-escape text."""
    return html_mod.escape(str(text))


def to_html(report: PlaylistReport) -> str:
    """Generate a self-contained HTML report with radar charts per artist."""
    artist_cards = []
    chart_scripts = []

    for idx, a in enumerate(report.artists):
        ev = a.evaluation
        if not ev:
            continue
        card_html, chart_js = _build_artist_card_html(a, ev, idx)
        artist_cards.append(card_html)
        chart_scripts.append(chart_js)

    verdict_color = _VERDICT_COLORS_CSS.get("Verified Artist", "#666")
    health = report.health_score
    if health >= 80:
        health_color = "#22c55e"
    elif health >= 60:
        health_color = "#eab308"
    elif health >= 40:
        health_color = "#f97316"
    else:
        health_color = "#ef4444"

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
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ color: #1DB954; font-size: 1.8rem; margin-bottom: 0.5rem; }}
  .subtitle {{ color: #888; margin-bottom: 2rem; }}

  /* Playlist header */
  .playlist-header {{
    background: #1a1a1a; border-radius: 12px; padding: 1.5rem; margin-bottom: 2rem;
    border: 1px solid #333;
  }}
  .health-score {{
    font-size: 3rem; font-weight: 700; text-align: center; margin: 1rem 0;
  }}
  .health-label {{ text-align: center; color: #888; font-size: 0.9rem; }}

  /* Verdict summary */
  .verdict-grid {{
    display: flex; gap: 0.5rem; justify-content: center; margin: 1.5rem 0;
    flex-wrap: wrap;
  }}
  .verdict-pill {{
    padding: 0.4rem 1rem; border-radius: 20px; font-size: 0.85rem;
    font-weight: 600; text-align: center;
  }}

  /* Artist cards */
  .artist-card {{
    background: #1a1a1a; border-radius: 12px; margin-bottom: 1.5rem;
    border: 1px solid #333; overflow: hidden;
  }}
  .card-header {{
    padding: 1.2rem 1.5rem; display: flex; justify-content: space-between;
    align-items: center; cursor: pointer; border-bottom: 1px solid #333;
  }}
  .card-header:hover {{ background: #222; }}
  .artist-name {{ font-size: 1.15rem; font-weight: 600; }}
  .verdict-badge {{
    padding: 0.3rem 0.8rem; border-radius: 16px; font-size: 0.8rem;
    font-weight: 600; color: #111;
  }}
  .card-body {{ padding: 1.5rem; display: none; }}
  .card-body.open {{ display: block; }}

  /* Two-column layout: chart + info */
  .scorecard-grid {{
    display: grid; grid-template-columns: 300px 1fr; gap: 2rem;
    align-items: start;
  }}
  @media (max-width: 768px) {{
    .scorecard-grid {{ grid-template-columns: 1fr; }}
  }}
  .chart-container {{ position: relative; width: 280px; height: 280px; }}

  /* Explanation */
  .explanation {{
    background: #222; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;
    font-size: 0.95rem; line-height: 1.5; border-left: 4px solid;
  }}

  /* Sources grid */
  .sources-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 0.5rem; margin: 1rem 0;
  }}
  .source-item {{
    padding: 0.4rem 0.6rem; border-radius: 6px; font-size: 0.8rem;
    text-align: center; font-weight: 500;
  }}
  .source-ok {{ background: #052e16; color: #4ade80; border: 1px solid #166534; }}
  .source-miss {{ background: #1c1c1c; color: #555; border: 1px solid #333; }}

  /* Signal category bars */
  .signal-bar-row {{
    display: flex; align-items: center; margin: 0.3rem 0; font-size: 0.85rem;
  }}
  .signal-label {{ width: 150px; color: #aaa; }}
  .signal-bar-bg {{
    flex: 1; height: 12px; background: #333; border-radius: 6px;
    overflow: hidden; margin: 0 0.8rem;
  }}
  .signal-bar-fill {{ height: 100%; border-radius: 6px; transition: width 0.3s; }}
  .signal-score {{ width: 50px; text-align: right; font-weight: 600; }}

  /* Flags */
  .section-title {{
    font-size: 0.9rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.05em; margin: 1.2rem 0 0.5rem 0; padding-bottom: 0.3rem;
    border-bottom: 1px solid #333;
  }}
  .flag-item {{ padding: 0.5rem 0; border-bottom: 1px solid #222; }}
  .flag-finding {{ font-weight: 500; }}
  .flag-detail {{ color: #888; font-size: 0.85rem; margin-top: 0.2rem; line-height: 1.4; }}
  .flag-meta {{ color: #666; font-size: 0.75rem; margin-top: 0.1rem; }}
  .strength-strong {{ color: #ef4444; }}
  .strength-moderate {{ color: #f97316; }}
  .strength-weak {{ color: #eab308; }}
  .green .strength-strong {{ color: #22c55e; }}
  .green .strength-moderate {{ color: #84cc16; }}
  .green .strength-weak {{ color: #a3e635; }}

  /* Data fields */
  .data-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 0.4rem; margin: 0.5rem 0;
  }}
  .data-field {{
    font-size: 0.8rem; padding: 0.3rem 0.5rem; background: #222;
    border-radius: 4px; color: #ccc;
  }}
  .data-field strong {{ color: #fff; }}
</style>
</head>
<body>
<div class="container">
  <h1>Spotify Audit Report</h1>
  <p class="subtitle">{_esc(report.playlist_name)} &mdash; {_esc(report.owner)} &mdash; {report.total_tracks} tracks, {report.total_unique_artists} artists</p>

  <div class="playlist-header">
    <div class="health-label">Playlist Health Score</div>
    <div class="health-score" style="color: {health_color}">{health}<span style="font-size: 1.2rem; color: #666">/100</span></div>
    <div class="verdict-grid">
      <span class="verdict-pill" style="background: #22c55e">Verified: {report.verified_artists}</span>
      <span class="verdict-pill" style="background: #84cc16">Likely Authentic: {report.likely_authentic}</span>
      <span class="verdict-pill" style="background: #eab308">Inconclusive: {report.inconclusive}</span>
      <span class="verdict-pill" style="background: #f97316">Suspicious: {report.suspicious}</span>
      <span class="verdict-pill" style="background: #ef4444; color: #fff">Likely Artificial: {report.likely_artificial}</span>
    </div>
  </div>

  {"".join(artist_cards)}

</div>

<script>
// Toggle card open/close
document.querySelectorAll('.card-header').forEach(h => {{
  h.addEventListener('click', () => {{
    const body = h.nextElementSibling;
    body.classList.toggle('open');
  }});
}});

// Render radar charts
{"".join(chart_scripts)}
</script>

<p style="text-align: center; color: #555; margin-top: 2rem; font-size: 0.8rem;">
  Report generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
</p>
</body>
</html>"""


def _build_artist_card_html(a: ArtistReport, ev: ArtistEvaluation, idx: int) -> tuple[str, str]:
    """Build HTML card + Chart.js script for one artist."""
    verdict_str = ev.verdict.value
    verdict_color = _VERDICT_COLORS_CSS.get(verdict_str, "#888")
    verdict_bg = _VERDICT_BG_CSS.get(verdict_str, "#1a1a1a")
    scores = ev.category_scores
    sources = ev.sources_reached
    ext = ev.external_data or ExternalData()

    # Threat category badge (if applicable)
    threat_html = ""
    if a.threat_category_name:
        threat_html = f' <span style="background: #333; color: #f97316; padding: 0.3rem 0.8rem; border-radius: 16px; font-size: 0.75rem; font-weight: 600; margin-left: 0.5rem">{_esc(a.threat_category_name)}</span>'

    # Build plain-English explanation
    explanation = _build_explanation(ev)

    # Source status
    source_html = ""
    for name, reached in sources.items():
        cls = "source-ok" if reached else "source-miss"
        label = name if reached else f"{name}"
        source_html += f'<span class="{cls}">{_esc(label)}</span>\n'

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

    # Key data fields
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

    card_html = f"""<div class="artist-card">
  <div class="card-header">
    <span class="artist-name">{_esc(a.artist_name)}</span>
    <span class="verdict-badge" style="background: {verdict_color}">{_esc(verdict_str)}</span>{threat_html}
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

    # Chart.js radar config
    labels = list(scores.keys())
    values = list(scores.values())
    chart_js = f"""
(function() {{
  const ctx = document.getElementById('{canvas_id}');
  if (!ctx) return;
  new Chart(ctx, {{
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
            font: {{ size: 11 }},
          }},
        }}
      }},
      plugins: {{
        legend: {{ display: false }},
      }},
    }}
  }});
}})();
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
    """Build the key data fields section."""
    fields: list[str] = []

    if ev.platform_presence.deezer_fans:
        fields.append(f"<strong>Deezer fans:</strong> {ev.platform_presence.deezer_fans:,}")
    if ext.genius_song_count:
        fields.append(f"<strong>Genius songs:</strong> {ext.genius_song_count}")
    if ext.genius_is_verified:
        fields.append("<strong>Genius:</strong> verified")
    if ext.genius_followers_count:
        fields.append(f"<strong>Genius followers:</strong> {ext.genius_followers_count:,}")
    if ext.discogs_physical_releases:
        fields.append(f"<strong>Physical releases:</strong> {ext.discogs_physical_releases}")
    if ext.discogs_total_releases:
        fields.append(f"<strong>Discogs releases:</strong> {ext.discogs_total_releases}")
    if ext.discogs_profile:
        fields.append(f"<strong>Bio:</strong> {len(ext.discogs_profile)} chars")
    if ext.discogs_realname:
        fields.append(f"<strong>Real name:</strong> {_esc(ext.discogs_realname)}")
    if ext.discogs_members:
        fields.append(f"<strong>Members:</strong> {_esc(', '.join(ext.discogs_members[:4]))}")
    if ext.setlistfm_total_shows:
        fields.append(f"<strong>Concerts:</strong> {ext.setlistfm_total_shows}")
    if ext.setlistfm_tour_names:
        fields.append(f"<strong>Tours:</strong> {_esc(', '.join(ext.setlistfm_tour_names[:3]))}")
    if ext.setlistfm_venue_countries:
        fields.append(f"<strong>Countries:</strong> {_esc(', '.join(ext.setlistfm_venue_countries[:5]))}")
    if ext.musicbrainz_type:
        fields.append(f"<strong>Type:</strong> {_esc(ext.musicbrainz_type)}")
    if ext.musicbrainz_country:
        fields.append(f"<strong>Country:</strong> {_esc(ext.musicbrainz_country)}")
    if ext.musicbrainz_begin_date:
        fields.append(f"<strong>Active since:</strong> {_esc(ext.musicbrainz_begin_date)}")
    if ext.musicbrainz_isnis:
        fields.append(f"<strong>ISNI:</strong> {_esc(ext.musicbrainz_isnis[0])}")
    if ext.musicbrainz_ipis:
        fields.append(f"<strong>IPI:</strong> {_esc(ext.musicbrainz_ipis[0])}")
    if ext.musicbrainz_genres:
        fields.append(f"<strong>Genres:</strong> {_esc(', '.join(ext.musicbrainz_genres[:4]))}")

    # Social links
    social = []
    if ext.genius_facebook_name:
        social.append("Facebook")
    if ext.genius_instagram_name:
        social.append("Instagram")
    if ext.genius_twitter_name:
        social.append("Twitter/X")
    for rt in ext.musicbrainz_urls:
        social.append(f"MB:{rt}")
    if social:
        fields.append(f"<strong>Social/Web:</strong> {_esc(', '.join(social[:6]))}")

    if not fields:
        return ""

    items = "\n".join(f'<span class="data-field">{f}</span>' for f in fields)
    return f"""<div class="section-title" style="color: #aaa">Key Data Fields</div>
<div class="data-grid">{items}</div>\n"""
