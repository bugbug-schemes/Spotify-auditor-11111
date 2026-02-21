import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { api } from './api';
import Dashboard from './pages/Dashboard';
import ReviewQueue from './pages/ReviewQueue';
import EntityDetail from './pages/EntityDetail';
import ScanHistory from './pages/ScanHistory';
import BlocklistManager from './pages/BlocklistManager';
import ReviewHistory from './pages/ReviewHistory';

function App() {
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    api.getQueueStats().then(s => setPendingCount(s.total_pending || 0)).catch(() => {});
    const interval = setInterval(() => {
      api.getQueueStats().then(s => setPendingCount(s.total_pending || 0)).catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <BrowserRouter basename="/cms">
      <div className="app">
        <aside className="sidebar">
          <h2>Entity Review CMS</h2>
          <nav>
            <NavLink to="/" end>Dashboard</NavLink>
            <NavLink to="/queue">
              Review Queue
              {pendingCount > 0 && <span className="badge">{pendingCount}</span>}
            </NavLink>
            <NavLink to="/scans">Scan History</NavLink>
            <NavLink to="/blocklists">Blocklists</NavLink>
            <NavLink to="/history">Audit Log</NavLink>
          </nav>
        </aside>
        <main className="main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/queue" element={<ReviewQueue />} />
            <Route path="/entity/:entityType/:entityId" element={<EntityDetail />} />
            <Route path="/scans" element={<ScanHistory />} />
            <Route path="/scans/:scanId" element={<ScanHistory />} />
            <Route path="/blocklists" element={<BlocklistManager />} />
            <Route path="/history" element={<ReviewHistory />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
