import { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api';
import ArtistCard from '../components/ArtistCard';
import { VERDICT_COLORS } from '../components/signalColors';

// Verdict order for sort (worst first = ascending)
const VERDICT_SORT_ORDER = {
  'Likely Artificial': 0,
  'Suspicious': 1,
  'Inconclusive': 2,
  'Insufficient Data': 2,
  'Conflicting Signals': 2,
  'Likely Authentic': 3,
  'Verified Artist': 4,
};

const VERDICT_FILTER_OPTIONS = [
  'All',
  'Verified Artist',
  'Likely Authentic',
  'Inconclusive',
  'Suspicious',
  'Likely Artificial',
];

// ---------------------------------------------------------------------------
// Verdict Breakdown Bar (spec Part 1)
// ---------------------------------------------------------------------------

function VerdictBar({ results, skippedCount }) {
  const counts = useMemo(() => {
    const c = {
      'Verified Artist': 0,
      'Likely Authentic': 0,
      'Inconclusive': 0,
      'Suspicious': 0,
      'Likely Artificial': 0,
    };
    for (const r of results) {
      const v = r.verdict || 'Inconclusive';
      if (v in c) c[v]++;
      else c['Inconclusive']++;
    }
    return c;
  }, [results]);

  const total = results.length + (skippedCount || 0);
  if (total === 0) return null;

  const segments = [
    { label: 'Verified Artist', count: counts['Verified Artist'], color: VERDICT_COLORS['Verified Artist'] },
    { label: 'Likely Authentic', count: counts['Likely Authentic'], color: VERDICT_COLORS['Likely Authentic'] },
    { label: 'Inconclusive', count: counts['Inconclusive'], color: VERDICT_COLORS['Inconclusive'] },
    { label: 'Suspicious', count: counts['Suspicious'], color: VERDICT_COLORS['Suspicious'] },
    { label: 'Likely Artificial', count: counts['Likely Artificial'], color: VERDICT_COLORS['Likely Artificial'] },
  ];

  // Add gray "Not Scanned" segment (spec Part 1)
  if (skippedCount > 0) {
    segments.push({ label: 'Not Scanned', count: skippedCount, color: VERDICT_COLORS['Not Scanned'] });
  }

  const flaggedCount = counts['Suspicious'] + counts['Likely Artificial'];

  return (
    <div className="verdict-bar-section">
      <div className="verdict-bar-label">Verdict Breakdown</div>
      <div className="verdict-bar">
        {segments.map(seg => {
          if (seg.count <= 0) return null;
          const pct = (seg.count / total) * 100;
          return (
            <div
              key={seg.label}
              className="verdict-bar-segment"
              style={{ width: `${pct}%`, background: seg.color }}
              title={`${seg.label}: ${seg.count}`}
            >
              {pct >= 8 && seg.count}
            </div>
          );
        })}
      </div>
      <div className="verdict-bar-legend">
        {segments.map(seg => (
          seg.count > 0 && (
            <span key={seg.label} className="verdict-legend-item">
              <span className="verdict-legend-dot" style={{ background: seg.color }} />
              {seg.label} ({seg.count})
            </span>
          )
        ))}
      </div>

      {/* Nested Threat Breakdown (spec Part 1) */}
      {flaggedCount > 0 && (
        <ThreatBreakdown results={results} flaggedCount={flaggedCount} />
      )}
    </div>
  );
}

function ThreatBreakdown({ results, flaggedCount }) {
  const threatCounts = useMemo(() => {
    const c = {};
    for (const r of results) {
      if (r.threat_category && r.threat_category !== 'None') {
        c[r.threat_category] = (c[r.threat_category] || 0) + 1;
      }
    }
    return c;
  }, [results]);

  const threatColors = {
    'PFC Ghost Artist': '#f97316',
    'PFC + AI Hybrid': '#f97316',
    'Independent AI Artist': '#a78bfa',
    'AI Fraud Farm': '#ef4444',
    'AI Impersonation': '#ec4899',
  };

  const entries = Object.entries(threatCounts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;

  return (
    <div className="threat-breakdown">
      <div className="threat-breakdown-connector" />
      <div className="threat-breakdown-label">
        Threat Breakdown
        <span className="threat-breakdown-subtitle">
          {flaggedCount} artists flagged as Suspicious or Likely Artificial
        </span>
      </div>
      <div className="threat-breakdown-items">
        {entries.map(([name, count]) => (
          <span key={name} className="threat-breakdown-item" style={{ color: threatColors[name] || '#888' }}>
            <span className="threat-breakdown-dot" style={{ background: threatColors[name] || '#888' }} />
            {name} ({count})
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sort & Filter Controls (spec Part 2)
// ---------------------------------------------------------------------------

function SortFilterControls({ sortBy, onSortChange, filterVerdict, onFilterChange }) {
  return (
    <div className="sort-filter-controls">
      <div className="sort-controls">
        <span className="sort-label">Sort:</span>
        <button
          className={`sort-btn ${sortBy === 'score-asc' ? 'sort-btn--active' : ''}`}
          onClick={() => onSortChange('score-asc')}
        >
          Score \u2191
        </button>
        <button
          className={`sort-btn ${sortBy === 'score-desc' ? 'sort-btn--active' : ''}`}
          onClick={() => onSortChange('score-desc')}
        >
          Score \u2193
        </button>
        <button
          className={`sort-btn ${sortBy === 'alpha' ? 'sort-btn--active' : ''}`}
          onClick={() => onSortChange('alpha')}
        >
          A-Z
        </button>
      </div>
      <div className="filter-controls">
        {VERDICT_FILTER_OPTIONS.map(opt => (
          <button
            key={opt}
            className={`filter-btn ${filterVerdict === opt ? 'filter-btn--active' : ''}`}
            onClick={() => onFilterChange(opt)}
            style={opt !== 'All' && filterVerdict === opt ? {
              borderColor: VERDICT_COLORS[opt],
              color: VERDICT_COLORS[opt],
            } : undefined}
          >
            {opt === 'All' ? 'All' : opt}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scan Detail View
// ---------------------------------------------------------------------------

function ScanDetail({ detail }) {
  const [sortBy, setSortBy] = useState('score-asc');
  const [filterVerdict, setFilterVerdict] = useState('All');

  const results = detail.results || [];
  const summary = detail.summary || {};
  const skippedCount = summary.timed_out_count || detail.skipped_count || detail.skipped_artists?.length || 0;
  const analyzedCount = summary.analyzed_count || results.length;
  const totalArtists = summary.total_playlist_artists || (analyzedCount + skippedCount);

  // Sort and filter results
  const displayResults = useMemo(() => {
    let filtered = results;
    if (filterVerdict !== 'All') {
      filtered = results.filter(r => r.verdict === filterVerdict);
    }

    const sorted = [...filtered];
    switch (sortBy) {
      case 'score-asc':
        sorted.sort((a, b) => (a.score ?? 0) - (b.score ?? 0));
        break;
      case 'score-desc':
        sorted.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
        break;
      case 'alpha':
        sorted.sort((a, b) => (a.artist_name || '').localeCompare(b.artist_name || ''));
        break;
    }
    return sorted;
  }, [results, sortBy, filterVerdict]);

  return (
    <div>
      <div className="page-header">
        <div>
          <Link to="/scans" style={{ fontSize: 12, color: 'var(--text-dim)' }}>&larr; All scans</Link>
          <h1 style={{ marginTop: 4 }}>{detail.playlist_name || 'Scan'} #{detail.id}</h1>
        </div>
      </div>

      {/* Summary card (spec Part 1) */}
      <div className="card scan-summary-card">
        <div className="scan-summary-metrics">
          <div className="scan-summary-stat">
            <span className="scan-summary-value">{analyzedCount}</span>
            <span className="scan-summary-label">Analyzed{totalArtists > analyzedCount ? ` of ${totalArtists}` : ''}</span>
          </div>
          {skippedCount > 0 && (
            <div className="scan-summary-stat scan-summary-stat--warning">
              <span className="scan-summary-value">{skippedCount} timed out {'\u26A0\uFE0F'}</span>
              <span className="scan-summary-label">Not scanned</span>
            </div>
          )}
          <div className="scan-summary-stat">
            <span className="scan-summary-value">{summary.health_score ?? detail.health_score ?? '-'}</span>
            <span className="scan-summary-label">Health Score</span>
          </div>
        </div>

        {/* Verdict bar with Not Scanned segment */}
        <VerdictBar results={results} skippedCount={skippedCount} />

        {/* Methodology link (spec Part 1) */}
        <div className="methodology-link">
          Analyzed across 6 evidence categories using 7 data sources
        </div>
      </div>

      {/* Sort & Filter (spec Part 2) */}
      <SortFilterControls
        sortBy={sortBy}
        onSortChange={setSortBy}
        filterVerdict={filterVerdict}
        onFilterChange={setFilterVerdict}
      />

      {/* Artist cards */}
      {displayResults.length > 0 && (
        <div className="artist-card-list">
          {displayResults.map((r, i) => (
            <ArtistCard key={i} result={r} />
          ))}
        </div>
      )}
      {displayResults.length === 0 && results.length > 0 && (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 32 }}>
          No artists match the selected filter.
        </div>
      )}
      {results.length === 0 && (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 32 }}>
          No artist results for this scan.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ScanHistory Page
// ---------------------------------------------------------------------------

export default function ScanHistory() {
  const { scanId } = useParams();
  const [scans, setScans] = useState([]);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true);
    if (scanId) {
      api.getScan(scanId)
        .then(setDetail)
        .catch(e => setError(e.message))
        .finally(() => setLoading(false));
    } else {
      api.getScans()
        .then(data => setScans(data.scans || []))
        .catch(e => setError(e.message))
        .finally(() => setLoading(false));
    }
  }, [scanId]);

  if (error) return <div className="error">{error}</div>;
  if (loading) return <div className="loading">Loading...</div>;

  if (detail) {
    return <ScanDetail detail={detail} />;
  }

  return (
    <div>
      <div className="page-header"><h1>Scan History</h1></div>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr><th>ID</th><th>Playlist</th><th>Tier</th><th>Artists</th><th>Flagged</th><th>Date</th></tr>
          </thead>
          <tbody>
            {scans.length === 0 && (
              <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 32 }}>No scans yet</td></tr>
            )}
            {scans.map(s => (
              <tr key={s.id}>
                <td><Link to={`/scans/${s.id}`}>#{s.id}</Link></td>
                <td>{s.playlist_name || '-'}</td>
                <td>{s.scan_tier || '-'}</td>
                <td>{s.artist_count}</td>
                <td style={{ color: s.flagged_count > 0 ? 'var(--red)' : 'var(--text-dim)' }}>{s.flagged_count || 0}</td>
                <td style={{ color: 'var(--text-dim)', fontSize: 12 }}>{s.started_at ? new Date(s.started_at).toLocaleDateString() : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
