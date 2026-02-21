import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [health, setHealth] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([
      api.getQueueStats(),
      api.getApiHealth(24),
    ]).then(([s, h]) => {
      setStats(s);
      setHealth(h);
    }).catch(e => setError(e.message));
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!stats) return <div className="loading">Loading dashboard...</div>;

  const breakdown = stats.action_breakdown_30d || {};

  return (
    <div>
      <div className="page-header">
        <h1>Dashboard</h1>
        <button className="btn-green" onClick={() => {
          api.checkThresholds().then(() => window.location.reload());
        }}>Re-check Thresholds</button>
      </div>

      <div className="grid-3">
        <div className="card stat-card">
          <div className="value">{stats.total_pending}</div>
          <div className="label">Pending Review</div>
        </div>
        <div className="card stat-card">
          <div className="value">{stats.total_deferred}</div>
          <div className="label">Deferred</div>
        </div>
        <div className="card stat-card">
          <div className="value">{stats.total_reviewed}</div>
          <div className="label">Reviewed</div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <h3 style={{ marginBottom: 12, fontSize: 14 }}>Pending by Type</h3>
          <table>
            <thead><tr><th>Type</th><th>Count</th></tr></thead>
            <tbody>
              {Object.entries(stats.pending || {}).map(([type, count]) => (
                <tr key={type}>
                  <td>{type}</td>
                  <td><Link to={`/queue?entity_type=${type}`}>{count}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3 style={{ marginBottom: 12, fontSize: 14 }}>Review Velocity</h3>
          <table>
            <thead><tr><th>Period</th><th>Reviews</th></tr></thead>
            <tbody>
              <tr><td>Last 7 days</td><td>{stats.reviews_last_7_days}</td></tr>
              <tr><td>Last 30 days</td><td>{stats.reviews_last_30_days}</td></tr>
            </tbody>
          </table>
          {Object.keys(breakdown).length > 0 && (
            <>
              <h3 style={{ marginTop: 16, marginBottom: 8, fontSize: 14 }}>Actions (30d)</h3>
              <div style={{ display: 'flex', gap: 12 }}>
                {breakdown.confirmed_bad && <span className="status status-confirmed_bad">Confirmed: {breakdown.confirmed_bad}</span>}
                {breakdown.dismissed && <span className="status status-dim">Dismissed: {breakdown.dismissed}</span>}
                {breakdown.deferred && <span className="status status-deferred">Deferred: {breakdown.deferred}</span>}
              </div>
            </>
          )}
        </div>
      </div>

      {health.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: 12, fontSize: 14 }}>API Health (24h)</h3>
          <table>
            <thead>
              <tr><th>API</th><th>Calls</th><th>Success</th><th>Errors</th><th>Avg ms</th><th>Status</th></tr>
            </thead>
            <tbody>
              {health.map(h => (
                <tr key={h.api_name}>
                  <td>{h.api_name}</td>
                  <td>{h.total_calls}</td>
                  <td>{h.success_rate}%</td>
                  <td>{h.error_rate}%</td>
                  <td>{h.avg_response_ms}</td>
                  <td><span className={`status status-${h.health}`}>{h.health}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
