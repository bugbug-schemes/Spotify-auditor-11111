import { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { api } from '../api';

export default function EntityDetail() {
  const { entityType, entityId } = useParams();
  const navigate = useNavigate();
  const [entity, setEntity] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reviewNote, setReviewNote] = useState('');
  const [actionPending, setActionPending] = useState('');

  useEffect(() => {
    setLoading(true);
    api.getEntity(entityType, entityId, true)
      .then(setEntity)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [entityType, entityId]);

  const doReview = async (action) => {
    setActionPending(action);
    try {
      await api.submitReview(entityType, entityId, action, reviewNote);
      navigate('/queue');
    } catch (e) {
      setError(e.message);
      setActionPending('');
    }
  };

  if (error) return <div className="error">{error}</div>;
  if (loading || !entity) return <div className="loading">Loading entity...</div>;

  const statusClass = `status-${entity.threat_status}`;

  return (
    <div>
      <div className="page-header">
        <div>
          <Link to="/queue" style={{ fontSize: 12, color: 'var(--text-dim)' }}>&larr; Back to queue</Link>
          <h1 style={{ marginTop: 4 }}>{entity.name}</h1>
        </div>
        <span className="tag">{entityType}</span>
      </div>

      {/* Header card */}
      <div className="card">
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <div><span style={{ color: 'var(--text-dim)', fontSize: 12 }}>Status</span><br /><span className={`status ${statusClass}`}>{entity.threat_status}</span></div>
          <div><span style={{ color: 'var(--text-dim)', fontSize: 12 }}>Connected Artists</span><br /><strong>{entity.total_artist_count}</strong></div>
          <div><span style={{ color: 'var(--text-dim)', fontSize: 12 }}>Flagged</span><br /><strong style={{ color: 'var(--red)' }}>{entity.flagged_artist_count}</strong></div>
          <div><span style={{ color: 'var(--text-dim)', fontSize: 12 }}>First Seen</span><br />{entity.first_seen ? new Date(entity.first_seen).toLocaleDateString() : '-'}</div>
          <div><span style={{ color: 'var(--text-dim)', fontSize: 12 }}>Review Status</span><br /><span className={`status status-${entity.review_status}`}>{entity.review_status || 'not_queued'}</span></div>
        </div>
      </div>

      {/* Context clues */}
      {entity.context_clues?.length > 0 && (
        <div className="card">
          <h3 style={{ fontSize: 14, marginBottom: 10 }}>Context Clues</h3>
          {entity.context_clues.map((c, i) => (
            <div key={i} style={{ marginBottom: 6 }}>
              <span className={`severity-${c.severity}`}>{c.severity === 'critical' ? '!!' : c.severity === 'warning' ? '!' : '-'}</span>
              {' '}<span className={`severity-${c.severity}`}>{c.clue_text}</span>
            </div>
          ))}
        </div>
      )}

      {/* Investigation links */}
      {entity.investigation_links?.length > 0 && (
        <div className="card">
          <h3 style={{ fontSize: 14, marginBottom: 8 }}>Investigate</h3>
          <div className="investigation-links">
            {entity.investigation_links.map((link, i) => (
              <a key={i} href={link.url} target="_blank" rel="noopener noreferrer">{link.label}</a>
            ))}
          </div>
        </div>
      )}

      {/* Connected artists table */}
      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
          <h3 style={{ fontSize: 14 }}>Connected Artists ({entity.connected_artists?.length || 0})</h3>
        </div>
        <table>
          <thead>
            <tr><th>Artist</th><th>Verdict</th><th>Confidence</th><th>Category</th><th>Platforms</th></tr>
          </thead>
          <tbody>
            {(entity.connected_artists || []).map(a => {
              const verdictClass = a.latest_verdict === 'Likely Artificial' || a.latest_verdict === 'Suspicious'
                ? 'status-red' : a.latest_verdict === 'Verified Artist' ? 'status-green' : 'status-dim';
              return (
                <tr key={a.id}>
                  <td>{a.name}</td>
                  <td><span className={`status ${verdictClass}`}>{a.latest_verdict || '-'}</span></td>
                  <td>{a.latest_confidence || '-'}</td>
                  <td>{a.threat_category || '-'}</td>
                  <td>{a.platform_count || 0}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Review actions */}
      {entity.review_status === 'pending_review' || entity.review_status === 'deferred' ? (
        <div className="card">
          <h3 style={{ fontSize: 14, marginBottom: 10 }}>Review Decision</h3>
          <textarea
            placeholder="Add a note with your reasoning..."
            value={reviewNote}
            onChange={e => setReviewNote(e.target.value)}
            style={{ width: '100%', minHeight: 60, marginBottom: 12 }}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn-confirm" disabled={!!actionPending} onClick={() => doReview('confirmed_bad')}>
              {actionPending === 'confirmed_bad' ? 'Confirming...' : 'Confirm Bad Actor (A)'}
            </button>
            <button className="btn-dismiss" disabled={!!actionPending} onClick={() => doReview('dismissed')}>
              {actionPending === 'dismissed' ? 'Dismissing...' : 'Dismiss (D)'}
            </button>
            <button className="btn-defer" disabled={!!actionPending} onClick={() => doReview('deferred')}>
              {actionPending === 'deferred' ? 'Deferring...' : 'Defer (F)'}
            </button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8 }}>
            Keyboard: A = confirm, D = dismiss, F = defer
          </div>
        </div>
      ) : entity.review_action && (
        <div className="card">
          <h3 style={{ fontSize: 14, marginBottom: 6 }}>Review Complete</h3>
          <span className={`status status-${entity.review_action === 'confirmed_bad' ? 'red' : entity.review_action === 'dismissed' ? 'dim' : 'deferred'}`}>
            {entity.review_action}
          </span>
          {entity.reviewed_at && <span style={{ marginLeft: 12, color: 'var(--text-dim)', fontSize: 12 }}>{new Date(entity.reviewed_at).toLocaleString()}</span>}
          {entity.review_note && <p style={{ marginTop: 8, color: 'var(--text-dim)', whiteSpace: 'pre-wrap' }}>{entity.review_note}</p>}
        </div>
      )}

      {/* Review history */}
      {entity.review_history?.length > 0 && (
        <div className="card">
          <h3 style={{ fontSize: 14, marginBottom: 10 }}>Review History</h3>
          {entity.review_history.map((h, i) => (
            <div key={i} style={{ marginBottom: 8, fontSize: 13 }}>
              <span className={`status status-${h.action === 'confirmed_bad' ? 'red' : h.action === 'dismissed' ? 'dim' : 'deferred'}`}>{h.action}</span>
              <span style={{ marginLeft: 8, color: 'var(--text-dim)' }}>{new Date(h.timestamp).toLocaleString()}</span>
              {h.note && <span style={{ marginLeft: 8 }}>{h.note}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
