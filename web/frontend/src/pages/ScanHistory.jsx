import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api';

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
    return (
      <div>
        <div className="page-header">
          <div>
            <Link to="/scans" style={{ fontSize: 12, color: 'var(--text-dim)' }}>&larr; All scans</Link>
            <h1 style={{ marginTop: 4 }}>{detail.playlist_name || 'Scan'} #{detail.id}</h1>
          </div>
        </div>
        <div className="card">
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 13 }}>
            <div><span style={{ color: 'var(--text-dim)' }}>Tier:</span> {detail.scan_tier}</div>
            <div><span style={{ color: 'var(--text-dim)' }}>Artists:</span> {detail.artist_count}</div>
            <div><span style={{ color: 'var(--text-dim)' }}>Started:</span> {detail.started_at ? new Date(detail.started_at).toLocaleString() : '-'}</div>
            <div><span style={{ color: 'var(--text-dim)' }}>Completed:</span> {detail.completed_at ? new Date(detail.completed_at).toLocaleString() : 'In progress'}</div>
          </div>
        </div>
        {detail.results?.length > 0 && (
          <div className="card" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr><th>Artist</th><th>Verdict</th><th>Score</th><th>Confidence</th><th>Category</th></tr>
              </thead>
              <tbody>
                {detail.results.map((r, i) => {
                  const cls = r.verdict === 'Likely Artificial' || r.verdict === 'Suspicious' ? 'status-red'
                    : r.verdict === 'Verified Artist' || r.verdict === 'Likely Authentic' ? 'status-green' : 'status-dim';
                  return (
                    <tr key={i}>
                      <td>{r.artist_name}</td>
                      <td><span className={`status ${cls}`}>{r.verdict}</span></td>
                      <td>{r.score}</td>
                      <td>{r.confidence || '-'}</td>
                      <td>{r.threat_category || '-'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
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
