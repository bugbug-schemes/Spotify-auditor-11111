import { useState, useEffect, useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';

export default function ReviewQueue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(new Set());
  const [batchNote, setBatchNote] = useState('');
  const [showBatch, setShowBatch] = useState(false);

  const entityType = searchParams.get('entity_type') || '';
  const status = searchParams.get('status') || 'pending_review';

  const load = useCallback(() => {
    setLoading(true);
    const params = { status };
    if (entityType) params.entity_type = entityType;
    api.getQueue(params)
      .then(data => { setItems(data.items || []); setSelected(new Set()); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [entityType, status]);

  useEffect(() => { load(); }, [load]);

  const toggleSelect = (id) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === items.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(items.map((_, i) => i)));
    }
  };

  const doBatchAction = async (action) => {
    const entities = items
      .filter((_, i) => selected.has(i))
      .map(item => ({ entity_type: item.entity_type, entity_id: item.id }));
    if (!entities.length) return;
    try {
      await api.batchReview(action, entities, batchNote);
      setShowBatch(false);
      setBatchNote('');
      load();
    } catch (e) {
      setError(e.message);
    }
  };

  const setFilter = (key, val) => {
    const p = new URLSearchParams(searchParams);
    if (val) p.set(key, val); else p.delete(key);
    setSearchParams(p);
  };

  if (error) return <div className="error">{error}</div>;

  return (
    <div>
      <div className="page-header">
        <h1>Review Queue</h1>
        {selected.size > 0 && (
          <div style={{ display: 'flex', gap: 8 }}>
            <span style={{ color: 'var(--text-dim)', alignSelf: 'center' }}>{selected.size} selected</span>
            <button className="btn-confirm btn-sm" onClick={() => setShowBatch('confirmed_bad')}>Confirm All</button>
            <button className="btn-sm" onClick={() => setShowBatch('dismissed')}>Dismiss All</button>
            <button className="btn-defer btn-sm" onClick={() => setShowBatch('deferred')}>Defer All</button>
          </div>
        )}
      </div>

      <div className="filters">
        <label>Type:</label>
        <select value={entityType} onChange={e => setFilter('entity_type', e.target.value)}>
          <option value="">All</option>
          <option value="songwriter">Songwriters</option>
          <option value="label">Labels</option>
          <option value="publisher">Publishers</option>
        </select>
        <label>Status:</label>
        <select value={status} onChange={e => setFilter('status', e.target.value)}>
          <option value="pending_review">Pending Review</option>
          <option value="deferred">Deferred</option>
        </select>
      </div>

      {loading ? <div className="loading">Loading...</div> : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th><input type="checkbox" checked={selected.size === items.length && items.length > 0} onChange={toggleAll} /></th>
                <th>Name</th>
                <th>Type</th>
                <th>Connected Artists</th>
                <th>Status</th>
                <th>Threshold Crossed</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 32 }}>
                  Queue is empty
                </td></tr>
              )}
              {items.map((item, i) => (
                <tr key={`${item.entity_type}-${item.id}`}>
                  <td><input type="checkbox" checked={selected.has(i)} onChange={() => toggleSelect(i)} /></td>
                  <td>
                    <Link to={`/entity/${item.entity_type}/${item.id}`}>
                      {item.name}
                    </Link>
                  </td>
                  <td><span className="tag">{item.entity_type}</span></td>
                  <td>{item.artist_count}</td>
                  <td><span className={`status status-${item.threat_status}`}>{item.threat_status}</span></td>
                  <td style={{ color: 'var(--text-dim)', fontSize: 12 }}>
                    {item.threshold_crossed_at ? new Date(item.threshold_crossed_at).toLocaleDateString() : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showBatch && (
        <div className="modal-overlay" onClick={() => setShowBatch(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Batch {showBatch === 'confirmed_bad' ? 'Confirm' : showBatch === 'dismissed' ? 'Dismiss' : 'Defer'} ({selected.size} entities)</h3>
            <textarea
              placeholder="Add a note (optional)..."
              value={batchNote}
              onChange={e => setBatchNote(e.target.value)}
            />
            <div className="actions">
              <button onClick={() => setShowBatch(false)}>Cancel</button>
              <button
                className={showBatch === 'confirmed_bad' ? 'btn-confirm' : showBatch === 'deferred' ? 'btn-defer' : ''}
                onClick={() => doBatchAction(showBatch)}
              >
                {showBatch === 'confirmed_bad' ? 'Confirm Bad Actor' : showBatch === 'dismissed' ? 'Dismiss' : 'Defer'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
