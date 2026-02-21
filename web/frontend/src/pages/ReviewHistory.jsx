import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

export default function ReviewHistory() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [typeFilter, setTypeFilter] = useState('');

  useEffect(() => {
    setLoading(true);
    const params = {};
    if (typeFilter) params.entity_type = typeFilter;
    api.getHistory(params)
      .then(data => setEntries(data.entries || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [typeFilter]);

  if (error) return <div className="error">{error}</div>;

  return (
    <div>
      <div className="page-header"><h1>Audit Log</h1></div>

      <div className="filters">
        <label>Type:</label>
        <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}>
          <option value="">All</option>
          <option value="label">Labels</option>
          <option value="songwriter">Songwriters</option>
          <option value="publisher">Publishers</option>
        </select>
      </div>

      {loading ? <div className="loading">Loading...</div> : (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr><th>Date</th><th>Entity</th><th>Type</th><th>Action</th><th>Connections</th><th>Note</th></tr>
            </thead>
            <tbody>
              {entries.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 32 }}>No review history</td></tr>
              )}
              {entries.map(e => (
                <tr key={e.id}>
                  <td style={{ color: 'var(--text-dim)', fontSize: 12, whiteSpace: 'nowrap' }}>
                    {e.timestamp ? new Date(e.timestamp).toLocaleString() : '-'}
                  </td>
                  <td>
                    <Link to={`/entity/${e.entity_type}/${e.entity_id}`}>
                      #{e.entity_id}
                    </Link>
                  </td>
                  <td><span className="tag">{e.entity_type}</span></td>
                  <td>
                    <span className={`status status-${e.action === 'confirmed_bad' ? 'red' : e.action === 'dismissed' ? 'dim' : 'deferred'}`}>
                      {e.action}
                    </span>
                  </td>
                  <td>{e.connection_count_at_review}</td>
                  <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {e.note || '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
