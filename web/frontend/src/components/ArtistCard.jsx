import { useState, useMemo } from 'react';
import {
  getSignalLevel,
  getSignalColor,
  getSignalIcon,
  getScoreColor,
  getScoreIcon,
  getBlocklistColor,
  getVerdictColor,
  getVerdictBadgeBg,
  SIGNAL_COLORS,
  VERDICT_COLORS,
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

const SECTION_SUBTITLES = {
  'Platform Presence': 'Where the artist exists and who they are',
  'Fan Engagement': 'Do real people listen to this artist?',
  'Creative History': 'What have they actually made?',
  'IRL Presence': 'Does this artist exist in the physical world?',
  'Industry Signals': 'Formal music industry recognition',
  'Blocklist Status': 'Known bad actor matches',
};

// Tag-to-section classification (aligned with formatter.py _TAG_TO_AXIS)
const TAG_TO_SECTION = {
  // Platform Presence
  platform_presence: 'Platform Presence',
  not_found: 'Platform Presence',
  multi_platform: 'Platform Presence',
  single_platform: 'Platform Presence',
  wikipedia: 'Platform Presence',
  social_media: 'Platform Presence',
  no_social_media: 'Platform Presence',
  youtube_presence: 'Platform Presence',
  no_youtube: 'Platform Presence',
  verified_identity: 'Platform Presence',
  genius_verified: 'Platform Presence',
  bandcamp_presence: 'Platform Presence',
  authentic_bio: 'Platform Presence',
  authentic_photo: 'Platform Presence',
  generic_name: 'Platform Presence',
  press_coverage: 'Platform Presence',
  no_genres: 'Platform Presence',
  // Fan Engagement
  genuine_fans: 'Fan Engagement',
  low_fans: 'Fan Engagement',
  low_engagement: 'Fan Engagement',
  low_scrobble_engagement: 'Fan Engagement',
  streaming_pattern: 'Fan Engagement',
  youtube_disparity: 'Fan Engagement',
  listener_playlist_ratio: 'Fan Engagement',
  // Creative History
  catalog_albums: 'Creative History',
  genius_credits: 'Creative History',
  collaboration: 'Creative History',
  content_farm: 'Creative History',
  stream_farm: 'Creative History',
  empty_catalog: 'Creative History',
  cookie_cutter: 'Creative History',
  high_release_rate: 'Creative History',
  same_day_release: 'Creative History',
  // IRL Presence
  live_performance: 'IRL Presence',
  concert_history: 'IRL Presence',
  physical_release: 'IRL Presence',
  // Industry Signals
  industry_registered: 'Industry Signals',
  isni_registered: 'Industry Signals',
  ipi_registered: 'Industry Signals',
  pro_registered: 'Industry Signals',
  no_pro_registration: 'Industry Signals',
  normal_pro_split: 'Industry Signals',
  no_songwriter_share: 'Industry Signals',
  career_bio: 'Industry Signals',
  ai_bio: 'Industry Signals',
  suspicious_bio: 'Industry Signals',
  impersonation: 'Industry Signals',
  cowriter_network: 'Industry Signals',
  // Blocklist Status
  pfc_label: 'Blocklist Status',
  pfc_songwriter: 'Blocklist Status',
  pfc_publisher: 'Blocklist Status',
  known_ai_artist: 'Blocklist Status',
  known_ai_label: 'Blocklist Status',
  known_bad_actor: 'Blocklist Status',
  entity_confirmed_bad: 'Blocklist Status',
  entity_suspected: 'Blocklist Status',
  entity_cleared: 'Blocklist Status',
  entity_bad_label: 'Blocklist Status',
  entity_bad_songwriter: 'Blocklist Status',
  entity_bad_network: 'Blocklist Status',
  isrc_pfc_registrant: 'Blocklist Status',
  ai_generated_image: 'Blocklist Status',
  ai_generated_music: 'Blocklist Status',
  stock_photo: 'Blocklist Status',
  deezer_ai_clear: 'Blocklist Status',
};

const SOURCE_TO_SECTION = {
  Deezer: 'Fan Engagement',
  MusicBrainz: 'Industry Signals',
  Genius: 'Creative History',
  Catalog: 'Creative History',
  Discogs: 'IRL Presence',
  'Setlist.fm': 'IRL Presence',
  'Last.fm': 'Fan Engagement',
  Wikipedia: 'Platform Presence',
  Songkick: 'IRL Presence',
  YouTube: 'Platform Presence',
  Blocklist: 'Blocklist Status',
  'Entity DB': 'Blocklist Status',
  'PRO Registry': 'Industry Signals',
  Spotify: 'Platform Presence',
  'pre-check': 'Blocklist Status',
};

const SECTION_ICONS = {
  'Platform Presence': '\uD83C\uDF10',
  'Fan Engagement': '\uD83D\uDC65',
  'Creative History': '\uD83C\uDFB5',
  'IRL Presence': '\uD83C\uDFE4',
  'Industry Signals': '\uD83C\uDFAD',
  'Blocklist Status': '\uD83D\uDEE1',
};

// Platform profile URL builders
const PLATFORM_URLS = {
  Deezer: (ext) => ext?.deezer_id ? `https://www.deezer.com/artist/${ext.deezer_id}` : null,
  MusicBrainz: (ext) => ext?.musicbrainz_id ? `https://musicbrainz.org/artist/${ext.musicbrainz_id}` : null,
  Genius: (ext) => ext?.genius_url || null,
  'Last.fm': (ext, name) => name ? `https://www.last.fm/music/${encodeURIComponent(name)}` : null,
  Discogs: (ext) => ext?.discogs_id ? `https://www.discogs.com/artist/${ext.discogs_id}` : null,
  'Setlist.fm': (ext) => ext?.setlistfm_mbid ? `https://www.setlist.fm/setlists/${ext.setlistfm_mbid}.html` : null,
  Wikipedia: (ext) => ext?.wikipedia_url || null,
  YouTube: (ext) => ext?.youtube_url || null,
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

// ---------------------------------------------------------------------------
// Section building — spec Part 6 & 7 rules
// ---------------------------------------------------------------------------

function buildSections(evidenceList) {
  const sections = {};
  for (const name of SECTION_ORDER) {
    sections[name] = { green: [], red: [], neutral: [] };
  }

  // Deduplicate: track findings to prevent duplicate bullets (Part 7, Rule 6)
  const seenFindings = new Set();

  for (const ev of evidenceList) {
    const key = (ev.finding || '').toLowerCase().trim();
    if (seenFindings.has(key) && key.length > 0) continue;
    seenFindings.add(key);

    const section = classifyEvidence(ev);
    const bucket = sections[section] || sections['Industry Signals'];

    if (ev.type === 'green_flag') {
      bucket.green.push(ev);
    } else if (ev.type === 'red_flag') {
      bucket.red.push(ev);
    } else {
      bucket.neutral.push(ev);
    }
  }

  // Sort within each bucket: strong first, then moderate, then weak (Part 7, Rule 8)
  const strengthOrder = { strong: 0, moderate: 1, weak: 2 };
  for (const name of SECTION_ORDER) {
    const s = sections[name];
    const sortFn = (a, b) => (strengthOrder[a.strength] ?? 1) - (strengthOrder[b.strength] ?? 1);
    s.green.sort(sortFn);
    s.red.sort(sortFn);
  }

  return sections;
}

function computeCategoryScores(sections, evidenceList, verdict) {
  // Point values per spec Part 4
  const PTS = { strong: 30, moderate: 15, weak: 5 };

  const scores = {};
  for (const name of SECTION_ORDER) {
    const s = sections[name];
    let total = 0;
    let maxPossible = 0;

    for (const ev of s.green) {
      const pts = PTS[ev.strength] || 5;
      total += pts;
      maxPossible += pts;
    }
    for (const ev of s.red) {
      const pts = PTS[ev.strength] || 5;
      total -= pts;
      maxPossible += pts;
    }
    for (const ev of s.neutral) {
      maxPossible += 5;
    }

    // Normalize to 0-100
    if (maxPossible > 0) {
      scores[name] = Math.max(0, Math.min(100, Math.round((total / maxPossible + 1) / 2 * 100)));
    } else {
      scores[name] = 0;
    }
  }

  // Blocklist is binary per spec: 100 = clean, any hit → deduct
  const bl = sections['Blocklist Status'];
  if (bl.red.length > 0) {
    scores['Blocklist Status'] = 0;
  } else {
    scores['Blocklist Status'] = 100;
  }

  return scores;
}

function countFlags(evidenceList) {
  let green = 0, red = 0;
  for (const ev of evidenceList) {
    if (ev.type === 'green_flag') green++;
    else if (ev.type === 'red_flag') red++;
  }
  return { green, red };
}

// Spec Part 2: Standardized verdict description templates
function getVerdictDescription(verdict, name, greenCount, redCount, topReason, platformCount) {
  const n = name || 'This artist';
  const platforms = platformCount || 0;
  switch (verdict) {
    case 'Verified Artist':
      return `${n} shows strong evidence of legitimacy across ${platforms} platforms.`;
    case 'Likely Authentic':
      return `${n} appears legitimate. ${greenCount} positive and ${redCount} negative signals.`;
    case 'Inconclusive':
    case 'Insufficient Data':
    case 'Conflicting Signals':
      return `Evidence on ${n} is mixed \u2014 ${greenCount} positive and ${redCount} negative signals.`;
    case 'Suspicious':
      return `${n} shows warning signs. Found on ${platforms} platforms with ${redCount} red flags.`;
    case 'Likely Artificial':
      return `${n} has strong indicators of being artificial.${topReason ? ` ${topReason}.` : ''}`;
    default:
      return '';
  }
}

// Extract the top red flag reason for Likely Artificial verdict description
function getTopRedReason(evidenceList) {
  for (const ev of evidenceList) {
    if (ev.type !== 'red_flag' || ev.strength !== 'strong') continue;
    if (ev.tags?.includes('pfc_label')) return 'PFC label match';
    if (ev.tags?.includes('known_ai_artist')) return 'Known AI artist match';
    if (ev.tags?.includes('content_farm')) return 'Content farm pattern';
    if (ev.tags?.includes('stream_farm')) return 'Stream farm pattern';
  }
  return '';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ScoreBadge({ score, verdict, confidence }) {
  const color = getVerdictColor(verdict);
  const bg = getVerdictBadgeBg(verdict);

  // Confidence visual treatment per spec Part 2
  let borderStyle = '2px solid';
  let opacity = 1;
  if (confidence === 'low') {
    borderStyle = '2px dashed';
    opacity = 0.7;
  } else if (confidence === 'medium') {
    // Standard — no change
  }
  // High = solid, full opacity (default)

  return (
    <div
      className="artist-score-badge"
      style={{
        background: bg,
        color,
        borderColor: color,
        borderStyle: borderStyle.split(' ')[1] || 'solid',
        borderWidth: '2px',
        opacity,
      }}
    >
      {score}
    </div>
  );
}

// Mini 6-segment category bar (spec Part 2)
function MiniCategoryBar({ scores }) {
  return (
    <div className="mini-category-bar" title="Category scores: Platform | Fan | Creative | IRL | Industry | Blocklist">
      {SECTION_ORDER.map(name => {
        const score = scores[name] ?? 0;
        const isBlocklist = name === 'Blocklist Status';
        const color = isBlocklist ? getBlocklistColor(score) : getScoreColor(score, score > 0);
        return (
          <div
            key={name}
            className="mini-category-segment"
            style={{ background: color }}
            title={`${name}: ${score}`}
          />
        );
      })}
    </div>
  );
}

// Platform checkmarks row with clickable links (spec Part 3)
// Three states: ✓ found (green), ✗ not found (dim), ⚠ error/timeout (amber)
function PlatformCheckmarks({ result, sources }) {
  const platformList = [
    { name: 'Deezer', key: 'Deezer', statusKey: 'deezer' },
    { name: 'MusicBrainz', key: 'MusicBrainz', statusKey: 'musicbrainz' },
    { name: 'Genius', key: 'Genius', statusKey: 'genius' },
    { name: 'Last.fm', key: 'Last.fm', statusKey: 'lastfm' },
    { name: 'Discogs', key: 'Discogs', statusKey: 'discogs' },
    { name: 'Setlist.fm', key: 'Setlist.fm', statusKey: 'setlistfm' },
    { name: 'Wikipedia', key: 'Wikipedia', statusKey: 'wikipedia' },
  ];

  const ext = result.external_data || {};
  const artistName = result.artist_name || '';
  const apiStatus = result.api_status || {};
  const profileUrls = result.profile_urls || {};

  return (
    <div className="platform-checkmarks">
      {platformList.map(({ name, key, statusKey }) => {
        const status = apiStatus[statusKey];
        const found = status === 'found' || (!status && sources?.[key]);
        const errored = status === 'error' || status === 'timeout';
        const skipped = status === 'skipped';

        // Use profile_urls from JSON, fallback to URL builders
        const urlBuilder = PLATFORM_URLS[key];
        const url = found
          ? (profileUrls[statusKey] || (urlBuilder ? urlBuilder(ext, artistName) : null))
          : null;

        let icon, color, title;
        if (found) {
          icon = '\u2713'; color = '#22c55e'; title = `${name}: Found`;
        } else if (errored) {
          icon = '\u26A0'; color = '#fbbf24'; title = `${name}: ${status === 'timeout' ? 'Timed out' : 'Error'}`;
        } else if (skipped) {
          icon = '\u2014'; color = '#9ca3af'; title = `${name}: Not checked`;
        } else {
          icon = '\u2717'; color = '#444'; title = `${name}: Not found`;
        }

        const content = (
          <span
            className={`platform-check-item${errored ? ' platform-check-item--warning' : ''}`}
            style={{ color, borderColor: color + '33' }}
            title={title}
          >
            <span className="platform-check-icon">{icon}</span>
            {name}
          </span>
        );

        if (url) {
          return (
            <a key={key} href={url} target="_blank" rel="noopener noreferrer" style={{ textDecoration: 'none' }}>
              {content}
            </a>
          );
        }
        return <span key={key}>{content}</span>;
      })}
    </div>
  );
}

// Signal item with accessibility icon
function SignalItem({ evidence, isNegativeLine }) {
  const level = getSignalLevel(evidence.type, evidence.strength);
  const color = isNegativeLine ? '#9ca3af' : getSignalColor(level);
  const icon = isNegativeLine ? '\u2014' : getSignalIcon(level);

  return (
    <div className="signal-item" style={{ color }}>
      <span className="signal-icon">{icon}</span>
      <span className="signal-content">
        <span className="signal-text">{evidence.finding}</span>
      </span>
    </div>
  );
}

// Section bar with 4-tier color + accessibility icon
function SectionBar({ name, score, isBlocklist }) {
  const color = isBlocklist ? getBlocklistColor(score) : getScoreColor(score, score > 0);
  const icon = isBlocklist
    ? (score >= 100 ? '\u2713' : '\u2717')
    : getScoreIcon(score, score > 0);
  const pct = Math.max(0, Math.min(100, score));
  const sectionIcon = SECTION_ICONS[name] || '';

  return (
    <div className="section-header-bar">
      <div className="section-bar-label">
        <span className="section-bar-name">
          {sectionIcon && <span className="section-bar-icon">{sectionIcon}</span>}
          {name}
        </span>
        <span className="section-bar-score" style={{ color }}>
          <span className="section-bar-indicator">{icon}</span> {score}
        </span>
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
  const matchedRule = result.matched_rule || '';
  const verdictColor = getVerdictColor(verdict);
  const flags = useMemo(() => countFlags(evidence), [evidence]);
  const topReason = useMemo(() => getTopRedReason(evidence), [evidence]);

  const categoryScores = useMemo(
    () => computeCategoryScores(sections, evidence, verdict),
    [sections, evidence, verdict],
  );

  // Count platforms for description template
  const platformCount = useMemo(() => {
    if (!sources) return 0;
    return Object.values(sources).filter(Boolean).length;
  }, [sources]);

  const descriptionText = getVerdictDescription(
    verdict, result.artist_name, flags.green, flags.red, topReason, platformCount
  );

  // Threat category tag only for Suspicious/Likely Artificial (spec Part 2)
  const showThreat = (verdict === 'Suspicious' || verdict === 'Likely Artificial')
    && threatCategory && threatCategory !== 'None';

  const sources = result.sources_reached || {};

  // Check if analysis is incomplete (any API errored/timed out) — Fix 1
  const apiStatus = result.api_status || {};
  const hasApiErrors = useMemo(() => {
    return Object.values(apiStatus).some(s => s === 'error' || s === 'timeout');
  }, [apiStatus]);
  const erroredCount = useMemo(() => {
    return Object.values(apiStatus).filter(s => s === 'error' || s === 'timeout').length;
  }, [apiStatus]);

  return (
    <div className="artist-card">
      {/* === COLLAPSED STATE (spec Part 2) === */}
      <div
        className={`artist-card-header ${expanded ? 'artist-card-header--expanded' : ''}`}
        onClick={() => setExpanded(!expanded)}
        role="button"
        tabIndex={0}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setExpanded(!expanded); }}
      >
        <ScoreBadge score={score} verdict={verdict} confidence={confidence} />

        <div className="artist-card-header-content">
          <span className="artist-card-name">
            {result.artist_name}
            {hasApiErrors && (
              <span
                className="artist-card-incomplete"
                title={`Incomplete analysis \u2014 ${erroredCount} data source${erroredCount > 1 ? 's' : ''} could not be reached`}
              >
                {' '}{'\u26A0'}
              </span>
            )}
          </span>
          <div className="artist-card-description">{descriptionText}</div>
          <div className="artist-card-tags">
            <span
              className="artist-card-tag"
              style={{
                background: getVerdictBadgeBg(verdict),
                color: verdictColor,
              }}
            >
              {verdict}
            </span>
            {showThreat && (
              <span
                className="artist-card-tag"
                style={{
                  background: 'rgba(139,148,158,0.15)',
                  color: '#8b949e',
                }}
              >
                {threatCategory}
              </span>
            )}
          </div>
          <MiniCategoryBar scores={categoryScores} />
        </div>

        <button
          className="artist-card-chevron"
          aria-label={expanded ? 'Collapse' : 'Expand'}
          tabIndex={-1}
        >
          {expanded ? '\u25B2' : '\u25BC'}
        </button>
      </div>

      {/* === EXPANDED STATE (spec Parts 3, 6, 7) === */}
      {expanded && (
        <div className="artist-card-body">
          {/* 1. Platform checkmarks row with clickable links */}
          <PlatformCheckmarks result={result} sources={sources} />

          {/* 1b. Matched decision tree rule (spec C.1) */}
          {matchedRule && (
            <div className="matched-rule" style={{ color: verdictColor }}>
              <span className="matched-rule-icon">
                {verdict === 'Suspicious' || verdict === 'Likely Artificial' ? '\u26A0' : '\u2139'}
              </span>
              <span className="matched-rule-text">
                {verdict === 'Suspicious' || verdict === 'Likely Artificial' ? 'Flagged by ' : 'Matched '}
                {matchedRule}
                {confidence && ` (${confidence} confidence)`}
              </span>
            </div>
          )}

          {/* 1c. Incomplete analysis warning (Fix 1) */}
          {hasApiErrors && (
            <div className="incomplete-warning">
              {'\u26A0'} Evidence incomplete \u2014 {erroredCount} data source{erroredCount > 1 ? 's' : ''} could not be reached
            </div>
          )}

          {/* 2. Radar chart + category sections layout */}
          <div className="artist-card-top">
            <RadarChart scores={categoryScores} color={verdictColor} />
            <div className="artist-card-sections-col">
              {/* 3. Six category sections */}
              {SECTION_ORDER.map(sectionName => {
                const s = sections[sectionName];
                const sectionScore = categoryScores[sectionName] ?? 0;
                const isBlocklist = sectionName === 'Blocklist Status';

                // Build ordered signal list: green first, neutral, red, then negative line (Part 7, Rule 8)
                const greenItems = s.green;
                const redItems = s.red;

                // Build consolidated negative bullet (Part 7, Rules 2-4)
                const negativeLines = [];
                if (sectionName !== 'Blocklist Status') {
                  for (const ev of redItems) {
                    if (ev.finding && /not found|no |0 /.test(ev.finding.toLowerCase())) {
                      negativeLines.push(ev.finding);
                    }
                  }
                }

                return (
                  <div key={sectionName} className="artist-section">
                    <SectionBar
                      name={sectionName}
                      score={sectionScore}
                      isBlocklist={isBlocklist}
                    />
                    <div className="artist-section-signals">
                      {/* Green flags first (spec Part 7 Rule 8) */}
                      {greenItems.map((ev, i) => (
                        <SignalItem key={`g-${i}`} evidence={ev} />
                      ))}
                      {/* Neutral items between green and red (spec Part 7 Rule 8) */}
                      {s.neutral.map((ev, i) => (
                        <SignalItem key={`n-${i}`} evidence={ev} />
                      ))}
                      {/* Red flags (non-"not found" items) */}
                      {redItems.filter(ev => !negativeLines.includes(ev.finding)).map((ev, i) => (
                        <SignalItem key={`r-${i}`} evidence={ev} />
                      ))}
                      {/* Consolidated negative line (Part 7, Rule 3) */}
                      {negativeLines.length > 0 && (
                        <SignalItem
                          evidence={{
                            finding: negativeLines.join(' \u00B7 '),
                            type: 'red_flag',
                            strength: 'weak',
                          }}
                          isNegativeLine
                        />
                      )}
                      {/* Blocklist: show each match separately (Part 6.6) */}
                      {isBlocklist && redItems.length === 0 && (
                        <SignalItem
                          evidence={{
                            finding: 'Clean across all blocklists',
                            type: 'green_flag',
                            strength: 'moderate',
                          }}
                        />
                      )}
                      {/* If section has no signals at all — name specific sources per spec Part 7 Rule 4 */}
                      {greenItems.length === 0 && redItems.length === 0 && !isBlocklist && (
                        <SignalItem
                          evidence={{
                            finding: sectionName === 'Platform Presence'
                              ? 'Not found on Deezer, YouTube, Bandcamp, Wikipedia, Genius'
                              : sectionName === 'Fan Engagement'
                              ? 'Not found on Last.fm \u00B7 0 Deezer fans'
                              : sectionName === 'Creative History'
                              ? 'No catalog data available from Deezer or Genius'
                              : sectionName === 'IRL Presence'
                              ? 'No concert history on Setlist.fm \u00B7 No physical releases on Discogs'
                              : sectionName === 'Industry Signals'
                              ? 'No MusicBrainz entry \u00B7 No ISNI/IPI codes \u00B7 No ASCAP/BMI registration'
                              : `No data for ${sectionName.toLowerCase()}`,
                            type: 'neutral',
                            strength: 'weak',
                          }}
                          isNegativeLine
                        />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
