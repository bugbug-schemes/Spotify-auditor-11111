const BASE = '/api/cms';

async function req(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Queue
  getQueue: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return req(`/queue?${qs}`);
  },
  getQueueStats: () => req('/queue/stats'),

  // Entities
  getEntity: (type, id, refreshClues = false) =>
    req(`/entities/${type}/${id}${refreshClues ? '?refresh_clues=1' : ''}`),
  getEntityNetwork: (type, id) => req(`/entities/${type}/${id}/network`),

  // Review actions
  submitReview: (type, id, action, note = '') =>
    req(`/entities/${type}/${id}/review`, {
      method: 'POST',
      body: JSON.stringify({ action, note }),
    }),
  addNote: (type, id, note) =>
    req(`/entities/${type}/${id}/note`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    }),
  createAlias: (type, id, targetType, targetId, relationship = 'alias', note = '') =>
    req(`/entities/${type}/${id}/alias`, {
      method: 'POST',
      body: JSON.stringify({ target_type: targetType, target_id: targetId, relationship, note }),
    }),
  batchReview: (action, entities, note = '') =>
    req('/batch-review', {
      method: 'POST',
      body: JSON.stringify({ action, entities, note }),
    }),

  // History
  getHistory: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return req(`/history?${qs}`);
  },

  // Scans
  getScans: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return req(`/scans?${qs}`);
  },
  getScan: (id) => req(`/scans/${id}`),
  getArtistHistory: (name) => req(`/scans/artist/${encodeURIComponent(name)}`),

  // Blocklists
  getBlocklists: () => req('/blocklists'),
  getBlocklist: (name) => req(`/blocklists/${name}`),
  addToBlocklist: (name, entityName, note = '') =>
    req(`/blocklists/${name}/add`, {
      method: 'POST',
      body: JSON.stringify({ name: entityName, note }),
    }),
  removeFromBlocklist: (name, entityName, note = '') =>
    req(`/blocklists/${name}/remove`, {
      method: 'POST',
      body: JSON.stringify({ name: entityName, note }),
    }),
  syncBlocklists: () => req('/blocklists/sync', { method: 'POST' }),

  // API health
  getApiHealth: (hours = 24) => req(`/api-health?hours=${hours}`),

  // Network
  getNetwork: (minConnections = 2) => req(`/network?min_connections=${minConnections}`),

  // Thresholds
  checkThresholds: () => req('/check-thresholds', { method: 'POST' }),
};
