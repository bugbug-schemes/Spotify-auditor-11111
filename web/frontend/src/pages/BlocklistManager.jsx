import { useState, useEffect } from 'react';
import { api } from '../api';

export default function BlocklistManager() {
  const [blocklists, setBlocklists] = useState(null);
  const [activeList, setActiveList] = useState(null);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [addName, setAddName] = useState('');
  const [addNote, setAddNote] = useState('');
  const [search, setSearch] = useState('');

  useEffect(() => {
    api.getBlocklists()
      .then(setBlocklists)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const loadList = (name) => {
    setActiveList(name);
    setLoading(true);
    api.getBlocklist(name)
      .then(data => setEntries(data.entries || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  const handleAdd = async () => {
    if (!addName.trim() || !activeList) return;
    try {
      await api.addToBlocklist(activeList, addName.trim(), addNote);
      setAddName('');
      setAddNote('');
      loadList(activeList);
    } catch (e) { setError(e.message); }
  };

  const handleRemove = async (name) => {
    if (!confirm(`Remove "${name}" from ${activeList}?`)) return;
    try {
      await api.removeFromBlocklist(activeList, name, 'Removed via CMS');
      loadList(activeList);
    } catch (e) { setError(e.message); }
  };

  const handleSync = async () => {
    try {
      await api.syncBlocklists();
      alert('Blocklists synced to disk.');
    } catch (e) { setError(e.message); }
  };

  if (error) return <div className="error">{error}</div>;

  const filtered = entries.filter(e => e.toLowerCase().includes(search.toLowerCase()));

  return (
    <div>
      <div className="page-header">
        <h1>Blocklist Manager</h1>
        <button className="btn-green" onClick={handleSync}>Sync to Disk</button>
      </div>

      {/* Blocklist tabs */}
      {blocklists && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          {Object.entries(blocklists).map(([name, info]) => (
            <button
              key={name}
              onClick={() => loadList(name)}
              style={{
                background: activeList === name ? 'var(--bg-hover)' : undefined,
                borderColor: activeList === name ? 'var(--green)' : undefined,
              }}
            >
              {name.replace('.json', '')} ({info.count})
            </button>
          ))}
        </div>
      )}

      {activeList && (
        <>
          {/* Add form */}
          <div className="card" style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12, color: 'var(--text-dim)' }}>Add entry</label>
              <input
                style={{ width: '100%', marginTop: 4 }}
                placeholder="Entity name..."
                value={addName}
                onChange={e => setAddName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAdd()}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12, color: 'var(--text-dim)' }}>Note (optional)</label>
              <input
                style={{ width: '100%', marginTop: 4 }}
                placeholder="Reason..."
                value={addNote}
                onChange={e => setAddNote(e.target.value)}
              />
            </div>
            <button className="btn-green" onClick={handleAdd}>Add</button>
          </div>

          {/* Search */}
          <div className="filters">
            <input
              placeholder="Search entries..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ width: 300 }}
            />
            <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>{filtered.length} entries</span>
          </div>

          {/* Entries */}
          {loading ? <div className="loading">Loading...</div> : (
            <div className="card" style={{ padding: 0 }}>
              <table>
                <thead><tr><th>Name</th><th style={{ width: 80 }}>Action</th></tr></thead>
                <tbody>
                  {filtered.map(name => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td><button className="btn-sm" onClick={() => handleRemove(name)}>Remove</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {!activeList && !loading && (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 32 }}>
          Select a blocklist above to browse
        </div>
      )}
    </div>
  );
}
