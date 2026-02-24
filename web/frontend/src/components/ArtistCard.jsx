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
// Section definitions
// ---------------------------------------------------------------------------

const SECTION_ORDER = [
  'Platform Presence',
  'Fan Engagement',
  'Creative History',
  'IRL Presence',
  'Industry Signals',
  'Blocklist Status',
];

const TAG_TO_SECTION = {
  // Platform Presence (includes YouTube, Wikipedia, social media, verified identity)
  multi_platform: 'Platform Presence',
  single_platform: 'Platform Presence',
  wikipedia: 'Platform Presence',
  social_media: 'Platform Presence',
  no_social_media: 'Platform Presence',
  genius_verified: 'Platform Presence',
  verified_identity: 'Platform Presence',
  youtube_presence: 'Platform Presence',
  no_youtube: 'Platform Presence',
  bandcamp_presence: 'Platform Presence',
  // Fan Engagement
  genuine_fans: 'Fan Engagement',
  low_fans: 'Fan Engagement',
  low_scrobble_engagement: 'Fan Engagement',
  listener_playlist_ratio: 'Fan Engagement',
  youtube_disparity: 'Fan Engagement',
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
  mood_word_titles: 'Creative History',
  // IRL Presence
  live_performance: 'IRL Presence',
  physical_release: 'IRL Presence',
  concert_history: 'IRL Presence',
  touring_geography: 'IRL Presence',
  named_tour: 'IRL Presence',
  // Industry Signals (includes bio, photo, identity, PRO, press)
  industry_registered: 'Industry Signals',
  isni_registered: 'Industry Signals',
  ipi_registered: 'Industry Signals',
  pro_registered: 'Industry Signals',
  no_pro_registration: 'Industry Signals',
  no_songwriter_share: 'Industry Signals',
  normal_pro_split: 'Industry Signals',
  career_bio: 'Industry Signals',
  authentic_bio: 'Industry Signals',
  ai_bio: 'Industry Signals',
  ai_generated_image: 'Industry Signals',
  stock_photo: 'Industry Signals',
  authentic_photo: 'Industry Signals',
  suspicious_bio: 'Industry Signals',
  impersonation: 'Industry Signals',
  ai_generated_music: 'Industry Signals',
  deezer_ai_clear: 'Industry Signals',
  press_coverage: 'Industry Signals',
  generic_name: 'Industry Signals',
  no_genres: 'Industry Signals',
  // Blocklist Status
  pfc_label: 'Blocklist Status',
  known_ai_artist: 'Blocklist Status',
  known_ai_label: 'Blocklist Status',
  pfc_songwriter: 'Blocklist Status',
  pfc_publisher: 'Blocklist Status',
  isrc_pfc_registrant: 'Blocklist Status',
  cowriter_network: 'Blocklist Status',
  entity_confirmed_bad: 'Blocklist Status',
  entity_suspected: 'Blocklist Status',
  entity_cleared: 'Blocklist Status',
  entity_bad_label: 'Blocklist Status',
  entity_bad_songwriter: 'Blocklist Status',
  entity_bad_network: 'Blocklist Status',
  known_bad_actor: 'Blocklist Status',
};

const SOURCE_TO_SECTION = {
  Deezer: 'Platform Presence',
  MusicBrainz: 'Platform Presence',
  Genius: 'Platform Presence',
  Catalog: 'Creative History',
  Discogs: 'IRL Presence',
  'Setlist.fm': 'IRL Presence',
  'Last.fm': 'Fan Engagement',
  Wikipedia: 'Platform Presence',
  Songkick: 'IRL Presence',
  Bandsintown: 'IRL Presence',
  YouTube: 'Platform Presence',
  Blocklist: 'Blocklist Status',
  'Entity DB': 'Blocklist Status',
  'PRO Registry': 'Industry Signals',
  Spotify: 'Platform Presence',
};

// Default "no data" fallbacks
const NO_DATA_SIGNALS = {
  'Platform Presence': { finding: 'Not found on Deezer, MusicBrainz, or Genius', source: 'Analysis' },
  'Fan Engagement': { finding: 'No fan engagement data from Last.fm or Deezer', source: 'Analysis' },
  'Creative History': { finding: 'No release history or creative credits found', source: 'Analysis' },
  'IRL Presence': { finding: 'No live performances on Setlist.fm and no physical releases on Discogs', source: 'Analysis' },
  'Industry Signals': { finding: 'No industry registrations (ISNI, IPI, PRO) found', source: 'Analysis' },
  'Blocklist Status': { finding: 'Clean across all blocklists', source: 'Blocklist' },
};

// Candidates for padding thin sections — only used when source is absent from ALL evidence
const SECTION_PAD_CANDIDATES = {
  'Platform Presence': [
    { src: 'Deezer', finding: 'Not found on Deezer' },
    { src: 'MusicBrainz', finding: 'Not found on MusicBrainz' },
    { src: 'Genius', finding: 'Not found on Genius' },
    { src: 'Wikipedia', finding: 'No Wikipedia article found' },
    { src: 'YouTube', finding: 'No YouTube channel found' },
  ],
  'Fan Engagement': [
    { src: 'Last.fm', finding: 'No Last.fm listener data found' },
  ],
  'Creative History': [],
  'IRL Presence': [
    { src: 'Setlist.fm', finding: 'No concerts found on Setlist.fm' },
    { src: 'Discogs', finding: 'No physical releases found on Discogs' },
    { src: 'Bandsintown', finding: 'No events found on Bandsintown' },
  ],
  'Industry Signals': [
    { src: 'PRO Registry', finding: 'No PRO registration found' },
  ],
  'Blocklist Status': [
    { src: 'Entity DB', finding: 'No prior intelligence in entity database' },
  ],
};

const SECTION_ICONS = {
  'Platform Presence': '\uD83C\uDF10',
  'Fan Engagement': '\uD83D\uDC65',
  'Creative History': '\uD83C\uDFB5',
  'IRL Presence': '\uD83C\uDFE4',
  'Industry Signals': '\uD83C\uDFAD',
  'Blocklist Status': '\uD83D\uDEE1\uFE0F',
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
  return 'Industry Signals';
}

function parseDurationToSecs(dur) {
  if (!dur) return null;
  const parts = dur.split(':');
  if (parts.length !== 2) return null;
  return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
}

// ---------------------------------------------------------------------------
// Creative metrics — extract from evidence and generate natural-language signals
// ---------------------------------------------------------------------------

function buildCreativeMetrics(evidenceList) {
  const metrics = {};

  for (const ev of evidenceList) {
    const f = ev.finding || '';
    const d = ev.detail || '';
    const combined = f + ' ' + d;

    const singlesMatch = combined.match(/(\d+)\s*singles?/i);
    if (singlesMatch) metrics.singles = parseInt(singlesMatch[1], 10);

    const albumsMatch = combined.match(/(\d+)\s*albums?/i);
    if (albumsMatch) metrics.albums = parseInt(albumsMatch[1], 10);

    const durationMatch = combined.match(/(?:avg|average)\s*(?:duration|track length|song duration)[:\s]*(\d+:\d{2})/i);
    if (durationMatch) metrics.avgDuration = durationMatch[1];

    const durationMatch2 = combined.match(/(\d:\d{2})\s*(?:avg|average)/i);
    if (durationMatch2 && !metrics.avgDuration) metrics.avgDuration = durationMatch2[1];

    const varianceMatch = combined.match(/(?:\u03C3|std|stdev|variance|deviation)[:\s=]*(\d+:\d{2}|\d+\.\d+s?)/i);
    if (varianceMatch) metrics.durationVariance = varianceMatch[1];

    const rangeMatch = combined.match(/range[:\s]*(\d+:\d{2})\s*[-\u2013]\s*(\d+:\d{2})/i);
    if (rangeMatch) metrics.durationRange = `${rangeMatch[1]}\u2013${rangeMatch[2]}`;

    const activeMatch = combined.match(/(?:active|career|recording)\s*(?:since|from|span)[:\s]*(\d{4})/i);
    if (activeMatch && !metrics.startYear) metrics.startYear = parseInt(activeMatch[1], 10);

    const firstReleaseMatch = combined.match(/(?:first|earliest)\s*(?:release|track|recording)[:\s]*(?:in\s*)?(\d{4})/i);
    if (firstReleaseMatch && !metrics.startYear) metrics.startYear = parseInt(firstReleaseMatch[1], 10);

    const dateRangeMatch = combined.match(/(\d{4})\s*[-\u2013]\s*(?:(\d{4})|present)/i);
    if (dateRangeMatch && !metrics.startYear) metrics.startYear = parseInt(dateRangeMatch[1], 10);
  }

  if (metrics.startYear) {
    const currentYear = new Date().getFullYear();
    metrics.yearsActive = Math.max(1, currentYear - metrics.startYear);
  }

  return metrics;
}

function buildCreativeSignals(metrics) {
  const signals = [];
  const yearsActive = metrics.yearsActive || null;

  // --- Duration signal (natural language) ---
  if (metrics.avgDuration) {
    const secs = parseDurationToSecs(metrics.avgDuration);
    let finding = `Avg song duration ${metrics.avgDuration}`;

    if (metrics.durationVariance) {
      const varSecs = parseDurationToSecs(metrics.durationVariance);
      if (varSecs !== null) {
        if (varSecs < 15) finding += ' with very low variation';
        else if (varSecs < 30) finding += ' with low variation';
        else if (varSecs < 60) finding += ' with normal variation';
        else finding += ' with high variation';
      }
    } else if (metrics.durationRange) {
      finding += ` (range: ${metrics.durationRange})`;
    }

    let type = 'green_flag';
    let strength = 'weak';
    if (secs !== null) {
      if (secs < 120) { type = 'red_flag'; strength = 'strong'; }
      else if (secs < 150) { type = 'red_flag'; strength = 'weak'; }
      else if (secs >= 180) { type = 'green_flag'; strength = 'moderate'; }
    }

    signals.push({ finding, source: 'Catalog', type, strength, tags: ['duration_analysis'], detail: '' });
  }

  return signals;
}

// ---------------------------------------------------------------------------
// Section building
// ---------------------------------------------------------------------------

function padSection(sectionName, signals, allEvidence, minCount = 3) {
  if (signals.length >= minCount) return signals;

  const candidates = SECTION_PAD_CANDIDATES[sectionName];
  if (!candidates || !candidates.length) return signals;

  // Only add "Not found on X" if X doesn't appear ANYWHERE in evidence
  const allSources = new Set(allEvidence.map(e => e.source));
  const padded = [...signals];

  for (const cand of candidates) {
    if (padded.length >= minCount) break;
    if (allSources.has(cand.src)) continue;
    padded.push({
      finding: cand.finding,
      source: cand.src,
      type: 'red_flag',
      strength: 'weak',
      tags: [],
      detail: '',
    });
  }

  return padded;
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

  // Inject creative metric signals if not already covered by existing findings
  const metrics = buildCreativeMetrics(evidenceList);
  const creativeSignals = buildCreativeSignals(metrics);
  const existingFindings = sections['Creative History'].map(e => (e.finding || '').toLowerCase()).join(' ');
  for (const cs of creativeSignals) {
    if (cs.tags.includes('duration_analysis') && /duration|avg/.test(existingFindings)) continue;
    sections['Creative History'].push(cs);
  }

  // Sort by strength: strong first, then moderate, then weak
  const strengthOrder = { strong: 0, moderate: 1, weak: 2 };
  for (const name of SECTION_ORDER) {
    sections[name].sort((a, b) => {
      const aOrder = strengthOrder[a.strength] ?? 1;
      const bOrder = strengthOrder[b.strength] ?? 1;
      return aOrder - bOrder;
    });
    if (sections[name].length > 5) {
      sections[name] = sections[name].slice(0, 5);
    }
  }

  // Pad thin sections to at least 3 signals
  for (const name of SECTION_ORDER) {
    sections[name] = padSection(name, sections[name], evidenceList);
  }

  // Final ensure: every section has at least 1 signal
  for (const name of SECTION_ORDER) {
    if (sections[name].length === 0) {
      const fallback = NO_DATA_SIGNALS[name];
      const fallbackType = name === 'Blocklist Status' ? 'green_flag' : 'red_flag';
      sections[name].push({
        finding: fallback.finding,
        source: fallback.source,
        type: fallbackType,
        strength: 'weak',
        tags: ['not_found'],
        detail: '',
      });
    }
  }

  return sections;
}

function computeCategoryScores(sections, evidenceList, verdict) {
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
  const showSource = evidence.source
    && evidence.source !== 'Analysis'
    && evidence.source !== 'Spotify';

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

function FlagSummary({ green, red, confidence }) {
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

  const evidence = useMemo(() => {
    if (!result.evidence_json) return [];
    if (typeof result.evidence_json === 'string') {
      try { return JSON.parse(result.evidence_json); }
      catch { return []; }
    }
    return Array.isArray(result.evidence_json) ? result.evidence_json : [];
  }, [result.evidence_json]);

  const sections = useMemo(() => buildSections(evidence), [evidence]);

  const verdict = result.verdict || 'Inconclusive';
  const score = result.score ?? 0;
  const confidence = result.confidence || '';
  const threatCategory = result.threat_category || '';
  const verdictColor = getVerdictColor(verdict);
  const summaryText = getSummaryText(verdict);
  const flags = useMemo(() => countFlags(evidence), [evidence]);

  const categoryScores = useMemo(
    () => computeCategoryScores(sections, evidence, verdict),
    [sections, evidence, verdict],
  );

  const tags = [verdict];
  if (threatCategory && threatCategory !== 'None' && threatCategory !== '') {
    tags.push(threatCategory);
  }

  return (
    <div className="artist-card">
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

      {expanded && (
        <div className="artist-card-body">
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
              />
            </div>
          </div>

          {SECTION_ORDER.map(sectionName => {
            const signals = sections[sectionName];
            const sectionScore = categoryScores[sectionName] ?? 0;

            return (
              <div key={sectionName} className="artist-section">
                <SectionBar name={sectionName} score={sectionScore} />
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
