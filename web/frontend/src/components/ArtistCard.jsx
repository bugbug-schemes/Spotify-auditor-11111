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

// Default "no data" signals per section (Task 3)
const NO_DATA_SIGNALS = {
  'Platform Presence': { finding: 'Not found on any secondary platform', source: 'Analysis' },
  'Fan Engagement': { finding: 'No fan engagement data found', source: 'Analysis' },
  'Creative History': { finding: 'No creative history data available', source: 'Analysis' },
  'IRL Presence': { finding: 'No live performances or physical releases found', source: 'Analysis' },
  'Online Identity': { finding: 'No online identity signals found', source: 'Analysis' },
  'Industry Signals': { finding: 'No industry registration data found', source: 'Analysis' },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function classifyEvidence(evidence) {
  // Classify a single evidence item into a section
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
    // Cap at 5 most impactful per section (Task 3)
    if (sections[name].length > 5) {
      sections[name] = sections[name].slice(0, 5);
    }
  }

  // Ensure every section has at least 1 signal (Task 3)
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

function computeCategoryScores(sections, evidenceList) {
  // Compute 0-100 scores per section based on evidence
  const scores = {};
  for (const name of SECTION_ORDER) {
    const items = sections[name];
    let pts = 50; // start neutral
    for (const ev of items) {
      if (ev.type === 'green_flag') {
        pts += ev.strength === 'strong' ? 20 : ev.strength === 'moderate' ? 12 : 5;
      } else if (ev.type === 'red_flag') {
        pts -= ev.strength === 'strong' ? 20 : ev.strength === 'moderate' ? 12 : 5;
      }
    }
    scores[name] = Math.max(0, Math.min(100, pts));
  }
  return scores;
}

function buildCreativeMetrics(evidenceList) {
  // Extract creative metrics from evidence findings
  const metrics = {};

  for (const ev of evidenceList) {
    const f = ev.finding || '';
    const d = ev.detail || '';
    const combined = f + ' ' + d;

    // Try to extract singles count
    const singlesMatch = combined.match(/(\d+)\s*singles?/i);
    if (singlesMatch) metrics.singles = parseInt(singlesMatch[1], 10);

    // Try to extract albums count
    const albumsMatch = combined.match(/(\d+)\s*albums?/i);
    if (albumsMatch) metrics.albums = parseInt(albumsMatch[1], 10);

    // Try to extract avg duration
    const durationMatch = combined.match(/(?:avg|average)\s*(?:duration|track length)[:\s]*(\d+:\d+)/i);
    if (durationMatch) metrics.avgDuration = durationMatch[1];

    // Duration from "avg X:XX" patterns
    const durationMatch2 = combined.match(/(\d:\d{2})\s*(?:avg|average)/i);
    if (durationMatch2) metrics.avgDuration = durationMatch2[1];

    // Track duration variance / std dev
    const varianceMatch = combined.match(/(?:σ|std|stdev|variance|deviation)[:\s=]*(\d+:\d+|\d+\.\d+s?)/i);
    if (varianceMatch) metrics.durationVariance = varianceMatch[1];

    // Duration range
    const rangeMatch = combined.match(/range[:\s]*(\d+:\d+)\s*[-–]\s*(\d+:\d+)/i);
    if (rangeMatch) {
      metrics.durationRange = `${rangeMatch[1]}–${rangeMatch[2]}`;
    }
  }

  return metrics;
}

function formatCreativeSignals(metrics, sectionSignals) {
  // Build additional creative metrics display items (Task 7)
  const extra = [];

  if (metrics.singles != null && metrics.albums != null) {
    const ratio = metrics.albums > 0
      ? (metrics.singles / metrics.albums).toFixed(1)
      : '∞ — no albums';
    extra.push({
      label: 'Singles-to-album ratio',
      value: ratio,
    });
  }

  if (metrics.avgDuration) {
    extra.push({
      label: 'Average song duration',
      value: metrics.avgDuration,
    });
  }

  if (metrics.durationVariance) {
    extra.push({
      label: 'Duration variance',
      value: `σ = ${metrics.durationVariance}`,
    });
  } else if (metrics.durationRange) {
    extra.push({
      label: 'Duration range',
      value: metrics.durationRange,
    });
  }

  return extra;
}

function getSummaryText(verdict, score) {
  // Task 5: positive summary text for legitimate artists
  switch (verdict) {
    case 'Verified Artist':
      return 'Strong cross-platform presence with genuine fan engagement and verified identity.';
    case 'Likely Authentic':
      return 'Multiple legitimacy indicators found across platforms with real fan activity.';
    case 'Inconclusive':
      return 'Mixed or insufficient evidence — further investigation recommended.';
    case 'Insufficient Data':
      return 'Too few data sources available to make a confident determination.';
    case 'Conflicting Signals':
      return 'Conflicting evidence found — positive and negative signals are balanced.';
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

  return (
    <div className="signal-item" style={{ color }}>
      <span className="signal-icon">{icon}</span>
      <span className="signal-text">{evidence.finding}</span>
    </div>
  );
}

function SectionBar({ name, score }) {
  const color = getScoreColor(score);
  const pct = Math.max(0, Math.min(100, score));

  return (
    <div className="section-header-bar">
      <div className="section-bar-label">
        <span className="section-bar-name">{name}</span>
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
      {metrics.map((m, i) => (
        <div key={i} className="creative-metric">
          <span className="creative-metric-label">{m.label}</span>
          <span className="creative-metric-value">{m.value}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ArtistCard Component
// ---------------------------------------------------------------------------

export default function ArtistCard({ result }) {
  const [expanded, setExpanded] = useState(false);

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

  // Compute category scores
  const categoryScores = useMemo(
    () => computeCategoryScores(sections, evidence),
    [sections, evidence],
  );

  // Creative metrics (Task 7)
  const creativeMetrics = useMemo(
    () => formatCreativeSignals(
      buildCreativeMetrics(evidence),
      sections['Creative History'],
    ),
    [evidence, sections],
  );

  const verdict = result.verdict || 'Inconclusive';
  const score = result.score ?? 0;
  const confidence = result.confidence || '';
  const threatCategory = result.threat_category || '';
  const verdictColor = getVerdictColor(verdict);
  const summaryText = getSummaryText(verdict, score);

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
          {/* Summary text (Task 5) */}
          <div className="artist-card-summary" style={{ color: verdictColor }}>
            {summaryText}
          </div>

          {/* Radar Chart (Task 2) — rendered at bottom per user request */}
          <RadarChart scores={categoryScores} color={verdictColor} />

          {/* Sections */}
          {SECTION_ORDER.map(sectionName => {
            const signals = sections[sectionName];
            const sectionScore = categoryScores[sectionName] ?? 0;

            return (
              <div key={sectionName} className="artist-section">
                <SectionBar name={sectionName} score={sectionScore} />

                {/* Creative History extra metrics (Task 7) */}
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
