import { useState, useMemo } from 'react';
import {
  getSignalLevel,
  getSignalColor,
  getSignalIcon,
  getScoreColor,
  getVerdictColor,
  getVerdictBadgeBg,
  SIGNAL_COLORS,
} from './signalColors';
import RadarChart from './RadarChart';

// ---------------------------------------------------------------------------
// Section definitions — ordered per Task 8
// ---------------------------------------------------------------------------

const SECTION_ORDER = [
  'Platform Presence',
  'Fan Engagement',
  'Creative History',
  'IRL Presence',
  'Online Identity',
  'Industry Signals',
];

// Maps evidence tags to the section they belong in (Task 6: IRL Presence combines live + discogs)
const TAG_TO_SECTION = {
  // Platform Presence
  multi_platform: 'Platform Presence',
  single_platform: 'Platform Presence',
  // Fan Engagement
  genuine_fans: 'Fan Engagement',
  low_fans: 'Fan Engagement',
  low_scrobble_engagement: 'Fan Engagement',
  listener_playlist_ratio: 'Fan Engagement',
  // Creative History
  catalog_albums: 'Creative History',
  empty_catalog: 'Creative History',
  content_farm: 'Creative History',
  stream_farm: 'Creative History',
  cookie_cutter: 'Creative History',
  high_release_rate: 'Creative History',
  same_day_release: 'Creative History',
  genius_credits: 'Creative History',
  collaboration: 'Creative History',
  // IRL Presence (combined live + discogs)
  live_performance: 'IRL Presence',
  physical_release: 'IRL Presence',
  concert_history: 'IRL Presence',
  touring_geography: 'IRL Presence',
  named_tour: 'IRL Presence',
  bandcamp_presence: 'IRL Presence',
  // Online Identity
  wikipedia: 'Online Identity',
  social_media: 'Online Identity',
  no_social_media: 'Online Identity',
  genius_verified: 'Online Identity',
  career_bio: 'Online Identity',
  verified_identity: 'Online Identity',
  no_genres: 'Online Identity',
  // Industry Signals
  industry_registered: 'Industry Signals',
  isni_registered: 'Industry Signals',
  ipi_registered: 'Industry Signals',
  pro_registered: 'Industry Signals',
  no_pro_registration: 'Industry Signals',
  pfc_label: 'Industry Signals',
  known_ai_artist: 'Industry Signals',
  known_ai_label: 'Industry Signals',
  pfc_songwriter: 'Industry Signals',
  pfc_publisher: 'Industry Signals',
  no_songwriter_share: 'Industry Signals',
  isrc_pfc_registrant: 'Industry Signals',
  cowriter_network: 'Industry Signals',
  // Entity DB
  entity_confirmed_bad: 'Industry Signals',
  entity_suspected: 'Industry Signals',
  entity_bad_label: 'Industry Signals',
  entity_bad_songwriter: 'Industry Signals',
  entity_bad_network: 'Industry Signals',
  // AI-specific
  ai_generated_image: 'Online Identity',
  ai_bio: 'Online Identity',
  stock_photo: 'Online Identity',
  authentic_photo: 'Online Identity',
  authentic_bio: 'Online Identity',
  suspicious_bio: 'Online Identity',
  ai_generated_music: 'Industry Signals',
  deezer_ai_clear: 'Industry Signals',
  // YouTube / Press
  youtube_presence: 'Online Identity',
  no_youtube: 'Online Identity',
  youtube_disparity: 'Fan Engagement',
  press_coverage: 'Online Identity',
  // Name patterns
  generic_name: 'Online Identity',
  mood_word_titles: 'Creative History',
};

// Source to section mapping for evidence without tags
const SOURCE_TO_SECTION = {
  Deezer: 'Platform Presence',
  MusicBrainz: 'Platform Presence',
  Genius: 'Platform Presence',
  Discogs: 'IRL Presence',
  'Setlist.fm': 'IRL Presence',
  'Last.fm': 'Fan Engagement',
  Wikipedia: 'Online Identity',
  Songkick: 'IRL Presence',
  Bandsintown: 'IRL Presence',
  YouTube: 'Online Identity',
  Blocklist: 'Industry Signals',
  'Entity DB': 'Industry Signals',
  'PRO Registry': 'Industry Signals',
  Spotify: 'Platform Presence',
};

// Default "no data" signals per section — absence is itself a signal
const NO_DATA_SIGNALS = {
  'Platform Presence': { finding: 'Not found on Deezer, MusicBrainz, or Genius', source: 'Analysis' },
  'Fan Engagement': { finding: 'No fan engagement data from Last.fm or Deezer', source: 'Analysis' },
  'Creative History': { finding: 'No release history or creative credits found', source: 'Analysis' },
  'IRL Presence': { finding: 'No live performances on Setlist.fm and no physical releases on Discogs', source: 'Analysis' },
  'Online Identity': { finding: 'No Wikipedia article, social media, or verified profiles found', source: 'Analysis' },
  'Industry Signals': { finding: 'No industry registrations (ISNI, IPI, PRO) found', source: 'Analysis' },
};

// Section icons for visual identification
const SECTION_ICONS = {
  'Platform Presence': '\uD83C\uDF10',  // globe
  'Fan Engagement': '\uD83D\uDC65',     // people
  'Creative History': '\uD83C\uDFB5',   // musical note
  'IRL Presence': '\uD83C\uDFE4',       // building
  'Online Identity': '\uD83D\uDD0D',    // magnifying glass
  'Industry Signals': '\uD83C\uDFAD',   // performing arts
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function classifyEvidence(evidence) {
  if (evidence.tags && evidence.tags.length > 0) {
    for (const tag of evidence.tags) {
      if (TAG_TO_SECTION[tag]) return TAG_TO_SECTION[tag];
    }
  }
  if (evidence.source && SOURCE_TO_SECTION[evidence.source]) {
    return SOURCE_TO_SECTION[evidence.source];
  }
  return 'Industry Signals'; // fallback
}

function buildSections(evidenceList) {
  const sections = {};
  for (const name of SECTION_ORDER) {
    sections[name] = [];
  }

  for (const ev of evidenceList) {
    const section = classifyEvidence(ev);
    if (sections[section]) {
      sections[section].push(ev);
    } else {
      sections['Industry Signals'].push(ev);
    }
  }

  // Sort signals within each section: strong first, then moderate, then weak
  const strengthOrder = { strong: 0, moderate: 1, weak: 2 };
  for (const name of SECTION_ORDER) {
    sections[name].sort((a, b) => {
      const aOrder = strengthOrder[a.strength] ?? 1;
      const bOrder = strengthOrder[b.strength] ?? 1;
      return aOrder - bOrder;
    });
    // Cap at 5 most impactful per section
    if (sections[name].length > 5) {
      sections[name] = sections[name].slice(0, 5);
    }
  }

  // Ensure every section has at least 1 signal
  for (const name of SECTION_ORDER) {
    if (sections[name].length === 0) {
      const fallback = NO_DATA_SIGNALS[name];
      sections[name].push({
        finding: fallback.finding,
        source: fallback.source,
        type: 'red_flag',
        strength: 'weak',
        tags: ['not_found'],
        detail: '',
      });
    }
  }

  return sections;
}

function computeCategoryScores(sections, evidenceList, verdict) {
  // Verdict-aware base scores: verified artists start higher so
  // their per-section scores reflect overall legitimacy properly
  const verdictBase = {
    'Verified Artist': 75,
    'Likely Authentic': 62,
    'Inconclusive': 50,
    'Insufficient Data': 45,
    'Conflicting Signals': 50,
    'Suspicious': 35,
    'Likely Artificial': 20,
  };
  const base = verdictBase[verdict] || 50;

  const scores = {};
  for (const name of SECTION_ORDER) {
    const items = sections[name];
    let pts = base;
    for (const ev of items) {
      if (ev.type === 'green_flag') {
        pts += ev.strength === 'strong' ? 18 : ev.strength === 'moderate' ? 10 : 5;
      } else if (ev.type === 'red_flag') {
        pts -= ev.strength === 'strong' ? 18 : ev.strength === 'moderate' ? 10 : 5;
      }
    }
    scores[name] = Math.max(0, Math.min(100, pts));
  }
  return scores;
}

function countFlags(evidenceList) {
  let green = 0;
  let red = 0;
  for (const ev of evidenceList) {
    if (ev.type === 'green_flag') green++;
    else if (ev.type === 'red_flag') red++;
  }
  return { green, red };
}

// ---------------------------------------------------------------------------
// Creative metrics helpers (Task 7)
// ---------------------------------------------------------------------------

function parseDurationToSecs(dur) {
  if (!dur) return null;
  const parts = dur.split(':');
  if (parts.length !== 2) return null;
  return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
}

function buildCreativeMetrics(evidenceList) {
  const metrics = {};

  for (const ev of evidenceList) {
    const f = ev.finding || '';
    const d = ev.detail || '';
    const combined = f + ' ' + d;

    // Singles count
    const singlesMatch = combined.match(/(\d+)\s*singles?/i);
    if (singlesMatch) metrics.singles = parseInt(singlesMatch[1], 10);

    // Albums count
    const albumsMatch = combined.match(/(\d+)\s*albums?/i);
    if (albumsMatch) metrics.albums = parseInt(albumsMatch[1], 10);

    // Avg duration
    const durationMatch = combined.match(/(?:avg|average)\s*(?:duration|track length|song duration)[:\s]*(\d+:\d{2})/i);
    if (durationMatch) metrics.avgDuration = durationMatch[1];

    const durationMatch2 = combined.match(/(\d:\d{2})\s*(?:avg|average)/i);
    if (durationMatch2 && !metrics.avgDuration) metrics.avgDuration = durationMatch2[1];

    // Track duration variance / std dev
    const varianceMatch = combined.match(/(?:\u03C3|std|stdev|variance|deviation)[:\s=]*(\d+:\d{2}|\d+\.\d+s?)/i);
    if (varianceMatch) metrics.durationVariance = varianceMatch[1];

    // Duration range
    const rangeMatch = combined.match(/range[:\s]*(\d+:\d{2})\s*[-\u2013]\s*(\d+:\d{2})/i);
    if (rangeMatch) {
      metrics.durationRange = `${rangeMatch[1]}\u2013${rangeMatch[2]}`;
    }

    // Years active
    const activeMatch = combined.match(/(?:active|career|recording)\s*(?:since|from|span)[:\s]*(\d{4})/i);
    if (activeMatch && !metrics.startYear) metrics.startYear = parseInt(activeMatch[1], 10);

    const firstReleaseMatch = combined.match(/(?:first|earliest)\s*(?:release|track|recording)[:\s]*(?:in\s*)?(\d{4})/i);
    if (firstReleaseMatch && !metrics.startYear) metrics.startYear = parseInt(firstReleaseMatch[1], 10);

    const dateRangeMatch = combined.match(/(\d{4})\s*[-\u2013]\s*(?:(\d{4})|present)/i);
    if (dateRangeMatch && !metrics.startYear) {
      metrics.startYear = parseInt(dateRangeMatch[1], 10);
    }
  }

  if (metrics.startYear) {
    const currentYear = new Date().getFullYear();
    metrics.yearsActive = Math.max(1, currentYear - metrics.startYear);
  }

  return metrics;
}

function formatCreativeSignals(metrics) {
  const extra = [];
  const yearsActive = metrics.yearsActive || null;

  if (metrics.singles != null && yearsActive) {
    const spy = parseFloat((metrics.singles / yearsActive).toFixed(1));
    let level = 'weak_positive';
    if (spy > 20) level = 'strong_negative';
    else if (spy > 10) level = 'weak_negative';
    else if (spy >= 2 && spy <= 8) level = 'weak_positive';
    else if (spy < 2) level = 'strong_positive';
    extra.push({ label: 'Singles per year', value: spy.toString(), level });
  }

  if (metrics.albums != null && yearsActive) {
    const apy = parseFloat((metrics.albums / yearsActive).toFixed(1));
    let level = 'weak_negative';
    if (apy >= 0.3) level = 'strong_positive';
    else if (apy > 0) level = 'weak_positive';
    extra.push({ label: 'Albums per year', value: apy.toString(), level });
  }

  if (metrics.singles != null && metrics.albums != null) {
    let value;
    let level;
    if (metrics.albums > 0) {
      const ratio = metrics.singles / metrics.albums;
      value = ratio.toFixed(1);
      if (ratio < 5) level = 'strong_positive';
      else if (ratio < 10) level = 'weak_positive';
      else level = 'weak_negative';
    } else {
      value = '\u221E \u2014 no albums';
      level = metrics.singles > 20 ? 'strong_negative' : 'weak_negative';
    }
    extra.push({ label: 'Singles-to-album ratio', value, level });
  }

  if (metrics.avgDuration) {
    const secs = parseDurationToSecs(metrics.avgDuration);
    let level = 'weak_positive';
    if (secs !== null) {
      if (secs < 120) level = 'strong_negative';
      else if (secs < 150) level = 'weak_negative';
      else if (secs >= 180) level = 'strong_positive';
    }
    extra.push({ label: 'Avg song duration', value: metrics.avgDuration, level });
  }

  if (metrics.durationVariance) {
    const varStr = metrics.durationVariance;
    let level = 'weak_positive';
    const varSecs = parseDurationToSecs(varStr);
    if (varSecs !== null) {
      if (varSecs < 15) level = 'strong_negative';
      else if (varSecs < 30) level = 'weak_negative';
      else level = 'strong_positive';
    }
    extra.push({ label: 'Duration variance', value: `\u03C3 = ${varStr}`, level });
  } else if (metrics.durationRange) {
    extra.push({ label: 'Duration range', value: metrics.durationRange, level: 'weak_positive' });
  }

  return extra;
}

function getSummaryText(verdict) {
  switch (verdict) {
    case 'Verified Artist':
      return 'Strong cross-platform presence with genuine fan engagement and verified identity.';
    case 'Likely Authentic':
      return 'Multiple legitimacy indicators found across platforms with real fan activity.';
    case 'Inconclusive':
      return 'Mixed or insufficient evidence \u2014 further investigation recommended.';
    case 'Insufficient Data':
      return 'Too few data sources available to make a confident determination.';
    case 'Conflicting Signals':
      return 'Conflicting evidence found \u2014 positive and negative signals are balanced.';
    case 'Suspicious':
      return 'Multiple warning signs detected. Patterns suggest possible artificial activity.';
    case 'Likely Artificial':
      return 'Strong indicators of artificial or manufactured activity detected.';
    default:
      return '';
  }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ScoreBadge({ score, verdict }) {
  const color = getVerdictColor(verdict);
  const bg = getVerdictBadgeBg(verdict);
  return (
    <div
      className="artist-score-badge"
      style={{ background: bg, color, borderColor: color }}
    >
      {score}
    </div>
  );
}

function SignalItem({ evidence }) {
  const level = getSignalLevel(evidence.type, evidence.strength);
  const color = getSignalColor(level);
  const icon = getSignalIcon(level);
  const showSource = evidence.source && evidence.source !== 'Analysis';

  return (
    <div className="signal-item" style={{ color }}>
      <span className="signal-icon">{icon}</span>
      <span className="signal-content">
        <span className="signal-text">{evidence.finding}</span>
        {showSource && (
          <span className="signal-source">{evidence.source}</span>
        )}
      </span>
    </div>
  );
}

function SectionBar({ name, score }) {
  const color = getScoreColor(score);
  const pct = Math.max(0, Math.min(100, score));
  const icon = SECTION_ICONS[name] || '';

  return (
    <div className="section-header-bar">
      <div className="section-bar-label">
        <span className="section-bar-name">
          {icon && <span className="section-bar-icon">{icon}</span>}
          {name}
        </span>
        <span className="section-bar-score" style={{ color }}>{score}</span>
      </div>
      <div className="section-bar-track">
        <div
          className="section-bar-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
    </div>
  );
}

function CreativeMetricsGrid({ metrics }) {
  if (!metrics.length) return null;
  return (
    <div className="creative-metrics-grid">
      {metrics.map((m, i) => {
        const color = m.level ? getSignalColor(m.level) : 'var(--text)';
        const icon = m.level ? getSignalIcon(m.level) : '';
        return (
          <div key={i} className="creative-metric">
            {icon && <span className="creative-metric-icon" style={{ color }}>{icon}</span>}
            <span className="creative-metric-label">{m.label}:</span>
            <span className="creative-metric-value" style={{ color }}>{m.value}</span>
          </div>
        );
      })}
    </div>
  );
}

function FlagSummary({ green, red, confidence, verdictColor }) {
  return (
    <div className="artist-card-flag-summary">
      <span className="flag-count flag-count--green">
        {green} green
      </span>
      <span className="flag-count-divider">/</span>
      <span className="flag-count flag-count--red">
        {red} red
      </span>
      {confidence && (
        <>
          <span className="flag-count-divider">&middot;</span>
          <span className="flag-count flag-count--conf">{confidence} conf.</span>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ArtistCard Component
// ---------------------------------------------------------------------------

export default function ArtistCard({ result, defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  // Parse evidence_json if it's a string
  const evidence = useMemo(() => {
    if (!result.evidence_json) return [];
    if (typeof result.evidence_json === 'string') {
      try { return JSON.parse(result.evidence_json); }
      catch { return []; }
    }
    return Array.isArray(result.evidence_json) ? result.evidence_json : [];
  }, [result.evidence_json]);

  // Build sections from evidence
  const sections = useMemo(() => buildSections(evidence), [evidence]);

  const verdict = result.verdict || 'Inconclusive';
  const score = result.score ?? 0;
  const confidence = result.confidence || '';
  const threatCategory = result.threat_category || '';
  const verdictColor = getVerdictColor(verdict);
  const summaryText = getSummaryText(verdict);
  const flags = useMemo(() => countFlags(evidence), [evidence]);

  // Compute category scores — verdict-aware
  const categoryScores = useMemo(
    () => computeCategoryScores(sections, evidence, verdict),
    [sections, evidence, verdict],
  );

  // Creative metrics
  const creativeMetrics = useMemo(
    () => formatCreativeSignals(buildCreativeMetrics(evidence)),
    [evidence],
  );

  // Classification tags
  const tags = [verdict];
  if (threatCategory && threatCategory !== 'None' && threatCategory !== '') {
    tags.push(threatCategory);
  }

  return (
    <div className="artist-card">
      {/* Header — collapsed state: score badge, name, tags, chevron */}
      <div
        className={`artist-card-header ${expanded ? 'artist-card-header--expanded' : ''}`}
        onClick={() => setExpanded(!expanded)}
        role="button"
        tabIndex={0}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setExpanded(!expanded); }}
      >
        <ScoreBadge score={score} verdict={verdict} />

        <div className="artist-card-header-content">
          <span className="artist-card-name">{result.artist_name}</span>
          <div className="artist-card-tags">
            {tags.map((tag, i) => (
              <span
                key={i}
                className="artist-card-tag"
                style={{
                  background: i === 0 ? getVerdictBadgeBg(verdict) : 'rgba(139,148,158,0.15)',
                  color: i === 0 ? verdictColor : '#8b949e',
                }}
              >
                {tag}
              </span>
            ))}
            {confidence && (
              <span className="artist-card-tag artist-card-tag--confidence">
                {confidence} confidence
              </span>
            )}
          </div>
        </div>

        <button
          className="artist-card-chevron"
          aria-label={expanded ? 'Collapse' : 'Expand'}
          tabIndex={-1}
        >
          {expanded ? '\u25B2' : '\u25BC'}
        </button>
      </div>

      {/* Expanded body */}
      {expanded && (
        <div className="artist-card-body">
          {/* Top area: radar chart + summary, side-by-side on desktop */}
          <div className="artist-card-top">
            <RadarChart scores={categoryScores} color={verdictColor} />
            <div className="artist-card-summary-area">
              <div className="artist-card-summary" style={{ color: verdictColor }}>
                {summaryText}
              </div>
              <FlagSummary
                green={flags.green}
                red={flags.red}
                confidence={confidence}
                verdictColor={verdictColor}
              />
            </div>
          </div>

          {/* Sections */}
          {SECTION_ORDER.map(sectionName => {
            const signals = sections[sectionName];
            const sectionScore = categoryScores[sectionName] ?? 0;

            return (
              <div key={sectionName} className="artist-section">
                <SectionBar name={sectionName} score={sectionScore} />

                {/* Creative History extra metrics */}
                {sectionName === 'Creative History' && (
                  <CreativeMetricsGrid metrics={creativeMetrics} />
                )}

                <div className="artist-section-signals">
                  {signals.map((ev, i) => (
                    <SignalItem key={i} evidence={ev} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
