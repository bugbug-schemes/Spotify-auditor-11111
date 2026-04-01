import { useState, useEffect, useMemo, useCallback } from 'react';
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
// Widths proportional to FULL playlist, percentages from analyzed_count only
// ---------------------------------------------------------------------------

function VerdictBar({ results, skippedCount }) {
  const analyzedCount = results.length;
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

  const total = analyzedCount + (skippedCount || 0);
  if (total === 0) return null;

  const segments = [
    { label: 'Verified Artist', count: counts['Verified Artist'], color: VERDICT_COLORS['Verified Artist'], isVerdict: true },
    { label: 'Likely Authentic', count: counts['Likely Authentic'], color: VERDICT_COLORS['Likely Authentic'], isVerdict: true },
    { label: 'Inconclusive', count: counts['Inconclusive'], color: VERDICT_COLORS['Inconclusive'], isVerdict: true },
    { label: 'Suspicious', count: counts['Suspicious'], color: VERDICT_COLORS['Suspicious'], isVerdict: true },
    { label: 'Likely Artificial', count: counts['Likely Artificial'], color: VERDICT_COLORS['Likely Artificial'], isVerdict: true },
  ];

  // Add gray "Not Scanned" segment (widths proportional to full playlist)
  if (skippedCount > 0) {
    segments.push({ label: 'Not Scanned', count: skippedCount, color: VERDICT_COLORS['Not Scanned'], isVerdict: false });
  }

  const flaggedCount = counts['Suspicious'] + counts['Likely Artificial'];

  return (
    <div className="verdict-bar-section">
      <div className="verdict-bar-label">Verdict Breakdown</div>
      <div className="verdict-bar">
        {segments.map(seg => {
          if (seg.count <= 0) return null;
          // Widths proportional to full playlist (analyzed + skipped)
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
        {segments.map(seg => {
          if (seg.count <= 0) return null;
          // Percentage labels: verdicts use analyzed_count; "Not Scanned" shows count only
          const legendText = seg.isVerdict && analyzedCount > 0
            ? `${seg.label}: ${Math.round((seg.count / analyzedCount) * 100)}% (${seg.count})`
            : `${seg.count} ${seg.label}`;
          return (
            <span key={seg.label} className="verdict-legend-item">
              <span className="verdict-legend-dot" style={{ background: seg.color }} />
              {legendText}
            </span>
          );
        })}
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
// Skipped Artists Notice (collapsible, with retry button)
// ---------------------------------------------------------------------------

function SkippedArtistsNotice({ skippedArtists, scanId, onRetryComplete }) {
  const [expanded, setExpanded] = useState(false);
  const [retryState, setRetryState] = useState('idle'); // idle | loading | polling | done | failed
  const [retryProgress, setRetryProgress] = useState({ current: 0, total: 0, message: '' });
  const [retryResult, setRetryResult] = useState(null);
  const [retryAttempts, setRetryAttempts] = useState(0);

  const maxRetries = 2;

  const handleRetry = useCallback(async () => {
    if (retryState === 'loading' || retryState === 'polling') return;

    setRetryState('loading');
    setRetryProgress({ current: 0, total: skippedArtists.length, message: `Retrying ${skippedArtists.length} artists...` });

    try {
      const { scan_id: retryId } = await api.retryScan(scanId);
      setRetryState('polling');

      // Poll for progress
      const poll = async () => {
        try {
          const status = await api.getScanStatus(retryId);
          setRetryProgress({
            current: status.current || 0,
            total: status.total || skippedArtists.length,
            message: status.message || 'Retrying...',
          });

          if (status.status === 'complete') {
            const recovered = (status.recovered_count || 0);
            const stillSkipped = status.skipped_json ? JSON.parse(status.skipped_json).length : 0;

            if (stillSkipped === 0) {
              setRetryState('done');
              setRetryResult({ type: 'all_recovered', recovered, stillSkipped: 0 });
            } else if (recovered > 0) {
              setRetryState('done');
              setRetryResult({ type: 'partial', recovered, stillSkipped });
            } else {
              setRetryState('failed');
              setRetryResult({ type: 'none', recovered: 0, stillSkipped });
            }
            setRetryAttempts(prev => prev + 1);
            if (onRetryComplete) onRetryComplete();
            return;
          }

          if (status.status === 'error' || status.phase === 'error') {
            setRetryState('failed');
            setRetryResult({ type: 'error', message: status.message || status.error || 'Retry failed' });
            return;
          }

          // Continue polling
          setTimeout(poll, 2000);
        } catch (err) {
          setRetryState('failed');
          setRetryResult({ type: 'error', message: err.message });
        }
      };
      poll();
    } catch (err) {
      setRetryState('failed');
      setRetryResult({ type: 'error', message: err.message });
    }
  }, [scanId, skippedArtists.length, retryState, onRetryComplete]);

  if (!skippedArtists || skippedArtists.length === 0) return null;

  // After all artists recovered, hide the notice
  if (retryState === 'done' && retryResult?.type === 'all_recovered') {
    return (
      <div className="skipped-notice skipped-notice--success">
        <span className="skipped-notice-icon">&#10003;</span>
        <span>All {retryResult.recovered} previously skipped artists have been recovered and added to the analysis.</span>
      </div>
    );
  }

  return (
    <div className="skipped-notice">
      <div className="skipped-notice-header">
        <div className="skipped-notice-summary">
          <span className="skipped-notice-icon">&#9888;</span>
          <span>
            {retryState === 'done' && retryResult?.type === 'partial'
              ? `${retryResult.stillSkipped} artists still could not be scanned (${retryResult.recovered} recovered)`
              : `${skippedArtists.length} artists could not be scanned`
            }
          </span>
        </div>
        <button
          className="skipped-notice-toggle"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
        >
          {expanded ? '\u25B2 Hide' : '\u25BC View skipped artists'}
        </button>
      </div>

      <div className="skipped-notice-subtitle">
        These artists were skipped due to timeouts or errors during scanning.
        They are not included in the analysis above.
      </div>

      {expanded && (
        <div className="skipped-notice-list">
          {skippedArtists.map((s, i) => (
            <div key={i} className="skipped-notice-item">
              <span className="skipped-notice-name">{s.artist_name || s.name || 'Unknown'}</span>
              <span className="skipped-notice-reason">{s.skip_reason || s.reason || 'Unknown error'}</span>
            </div>
          ))}
        </div>
      )}

      <div className="skipped-notice-actions">
        {retryState === 'idle' && retryAttempts < maxRetries && (
          <button className="retry-btn" onClick={handleRetry}>
            Retry Scan &rarr;
          </button>
        )}
        {(retryState === 'loading' || retryState === 'polling') && (
          <button className="retry-btn retry-btn--loading" disabled>
            <span className="retry-spinner" />
            {retryProgress.current > 0
              ? `Retrying... ${retryProgress.current}/${retryProgress.total} complete`
              : `Retrying ${retryProgress.total} artists...`
            }
          </button>
        )}
        {retryState === 'failed' && (
          <>
            <div className="skipped-notice-error">
              {retryResult?.type === 'none'
                ? 'Retry failed \u2014 all artists still could not be scanned. This may be due to API outages. Try again later.'
                : `Retry failed: ${retryResult?.message || 'Unknown error'}`
              }
            </div>
            {retryAttempts < maxRetries && (
              <button className="retry-btn" onClick={handleRetry}>
                Retry Again &rarr;
              </button>
            )}
          </>
        )}
        {retryState === 'done' && retryResult?.type === 'partial' && retryAttempts < maxRetries && (
          <button className="retry-btn" onClick={handleRetry}>
            Retry Again &rarr;
          </button>
        )}
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
          Score ↑
        </button>
        <button
          className={`sort-btn ${sortBy === 'score-desc' ? 'sort-btn--active' : ''}`}
          onClick={() => onSortChange('score-desc')}
        >
          Score ↓
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

function ScanDetail({ detail, onRefresh }) {
  const [sortBy, setSortBy] = useState('score-asc');
  const [filterVerdict, setFilterVerdict] = useState('All');

  const results = detail.results || [];
  const summary = detail.summary || {};
  const skippedArtists = detail.skipped_artists || [];
  const skippedCount = summary.skipped_count || summary.timed_out_count || skippedArtists.length || 0;
  const analyzedCount = summary.analyzed_count || results.length;
  const totalArtists = summary.total_playlist_artists || (analyzedCount + skippedCount);
  const flaggedCount = useMemo(() => results.filter(
    r => r.verdict === 'Suspicious' || r.verdict === 'Likely Artificial'
  ).length, [results]);

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

      {/* Summary card — metrics based on analyzed_count ONLY */}
      <div className="card scan-summary-card">
        <div className="scan-summary-metrics">
          <div className="scan-summary-stat">
            <span className="scan-summary-value">{analyzedCount}</span>
            <span className="scan-summary-label">
              Analyzed{totalArtists > analyzedCount ? ` of ${totalArtists}` : ''}
            </span>
          </div>
          <div className="scan-summary-stat">
            <span className="scan-summary-value" style={{ color: flaggedCount > 0 ? 'var(--red, #ef4444)' : undefined }}>
              {flaggedCount}
            </span>
            <span className="scan-summary-label">Flagged</span>
          </div>
        </div>

        {/* Verdict bar — widths from total, percentages from analyzed_count */}
        <VerdictBar results={results} skippedCount={skippedCount} />

        {/* Methodology link */}
        <div className="methodology-link">
          Analyzed across 6 evidence categories using 7 data sources &mdash;{' '}
          <a href="/methodology" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--blue)' }}>
            How does this work? ↗
          </a>
        </div>
      </div>

      {/* Skipped artists notice — separate from summary, with retry button */}
      {skippedCount > 0 && (
        <SkippedArtistsNotice
          skippedArtists={skippedArtists}
          scanId={detail.id}
          onRetryComplete={onRefresh}
        />
      )}

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

  const loadDetail = useCallback(() => {
    if (!scanId) return;
    setLoading(true);
    api.getScan(scanId)
      .then(setDetail)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [scanId]);

  useEffect(() => {
    if (scanId) {
      loadDetail();
    } else {
      setLoading(true);
      api.getScans()
        .then(data => setScans(data.scans || []))
        .catch(e => setError(e.message))
        .finally(() => setLoading(false));
    }
  }, [scanId, loadDetail]);

  if (error) return <div className="error">{error}</div>;
  if (loading) return <div className="loading">Loading...</div>;

  if (detail) {
    return <ScanDetail detail={detail} onRefresh={loadDetail} />;
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
