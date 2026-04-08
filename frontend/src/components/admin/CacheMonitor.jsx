import React, { useState, useEffect, useCallback } from 'react';
import { Database, Trash2, RefreshCw, Zap, Clock, BarChart3, Server } from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { authFetch } from '../../contexts/AuthContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const CacheMonitor = () => {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionKey, setActionKey] = useState(null);

  const fetchStats = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/admin/cache-stats`);
      if (res.ok) setStats(await res.json());
    } catch (e) {
      console.error('Cache stats error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  useEffect(() => {
    const interval = setInterval(fetchStats, 10000);
    return () => clearInterval(interval);
  }, [fetchStats]);

  const invalidateKey = async (key) => {
    setActionKey(key);
    try {
      const res = await authFetch(`${API}/admin/cache-invalidate/${key}`, { method: 'POST' });
      if (res.ok) await fetchStats();
    } catch (e) {
      console.error('Invalidate error:', e);
    } finally {
      setActionKey(null);
    }
  };

  const clearAll = async () => {
    setActionKey('__all__');
    try {
      const res = await authFetch(`${API}/admin/cache-clear`, { method: 'POST' });
      if (res.ok) await fetchStats();
    } catch (e) {
      console.error('Clear error:', e);
    } finally {
      setActionKey(null);
    }
  };

  const formatAge = (seconds) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
  };

  const formatBytes = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  };

  const formatUptime = (seconds) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m ${Math.round(seconds % 60)}s`;
  };

  const hitRate = stats?.hit_rate ?? 0;
  const hitRateColor = hitRate >= 80 ? 'text-emerald-400' : hitRate >= 50 ? 'text-amber-400' : 'text-red-400';
  const hitRateBg = hitRate >= 80 ? 'bg-emerald-500' : hitRate >= 50 ? 'bg-amber-500' : 'bg-red-500';

  return (
    <div className="p-6 space-y-5" data-testid="cache-monitor">
      {/* Summary Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-4" data-testid="cache-hit-rate">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="w-4 h-4 text-[#0052FF]" />
            <span className="text-slate-400 text-xs">Hit Rate</span>
          </div>
          <p className={`text-2xl font-black ${hitRateColor}`}>
            {loading ? '—' : `${hitRate}%`}
          </p>
        </Card>

        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-4" data-testid="cache-total-requests">
          <div className="flex items-center gap-2 mb-2">
            <BarChart3 className="w-4 h-4 text-cyan-400" />
            <span className="text-slate-400 text-xs">Requests</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : (stats?.total_requests ?? 0).toLocaleString()}
          </p>
          <div className="flex gap-2 mt-1 text-[10px]">
            <span className="text-emerald-400">{stats?.hits ?? 0} hits</span>
            <span className="text-red-400">{stats?.misses ?? 0} misses</span>
          </div>
        </Card>

        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-4" data-testid="cache-keys-count">
          <div className="flex items-center gap-2 mb-2">
            <Database className="w-4 h-4 text-purple-400" />
            <span className="text-slate-400 text-xs">Cached Keys</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : stats?.total_keys ?? 0}
          </p>
        </Card>

        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-4" data-testid="cache-uptime">
          <div className="flex items-center gap-2 mb-2">
            <Server className="w-4 h-4 text-amber-400" />
            <span className="text-slate-400 text-xs">Uptime</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : formatUptime(stats?.uptime_seconds ?? 0)}
          </p>
        </Card>
      </div>

      {/* Hit Rate Bar */}
      {stats && (
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs">
            <span className="text-slate-400">Cache Efficiency</span>
            <span className={hitRateColor}>{hitRate}%</span>
          </div>
          <div className="w-full bg-slate-700/50 rounded-full h-2">
            <div className={`h-2 rounded-full transition-all duration-700 ${hitRateBg}`} style={{ width: `${hitRate}%` }} />
          </div>
        </div>
      )}

      {/* Controls */}
      <div className="flex items-center justify-between">
        <h3 className="text-white text-sm font-semibold">Active Cache Entries</h3>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={fetchStats}
            className="text-[10px] h-7 px-2 bg-slate-800 border-slate-700 text-white"
            data-testid="cache-refresh-btn">
            <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button size="sm" variant="outline" onClick={clearAll}
            disabled={actionKey === '__all__' || !stats?.total_keys}
            className="text-[10px] h-7 px-2 bg-red-900/30 text-red-400 border-red-800/50 hover:bg-red-800/40"
            data-testid="cache-clear-all-btn">
            <Trash2 className="w-3 h-3 mr-1" />
            Clear All
          </Button>
        </div>
      </div>

      {/* Entries Table */}
      {stats?.entries?.length > 0 ? (
        <div className="overflow-x-auto rounded-xl border border-slate-700/40">
          <table className="w-full text-sm" data-testid="cache-entries-table">
            <thead>
              <tr className="border-b border-slate-700/40 bg-slate-800/40">
                <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Key</th>
                <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Age</th>
                <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Size</th>
                <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Status</th>
                <th className="text-right text-slate-400 text-xs font-medium px-4 py-2.5">Action</th>
              </tr>
            </thead>
            <tbody>
              {stats.entries.map(e => (
                <tr key={e.key} className="border-b border-slate-800/40 hover:bg-slate-800/30">
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <Clock className="w-3 h-3 text-slate-500 shrink-0" />
                      <span className="text-white text-xs font-mono">{e.key}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-slate-300 text-xs">{formatAge(e.age_seconds)}</td>
                  <td className="px-4 py-2.5 text-slate-300 text-xs">{formatBytes(e.size_bytes)}</td>
                  <td className="px-4 py-2.5">
                    {e.refreshing ? (
                      <Badge className="text-[9px] bg-amber-900/40 text-amber-400 border-amber-800">Refreshing</Badge>
                    ) : (
                      <Badge className="text-[9px] bg-emerald-900/40 text-emerald-400 border-emerald-800">Fresh</Badge>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <Button size="sm" variant="ghost"
                      onClick={() => invalidateKey(e.key)}
                      disabled={actionKey === e.key}
                      className="text-[10px] h-6 px-2 text-red-400 hover:text-red-300 hover:bg-red-900/20"
                      data-testid={`cache-invalidate-${e.key}`}>
                      <Trash2 className="w-3 h-3" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : !loading ? (
        <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-8 text-center">
          <Database className="w-8 h-8 text-slate-600 mx-auto mb-2" />
          <p className="text-slate-500 text-sm">No cached entries. Endpoints will populate on first request.</p>
        </Card>
      ) : null}
    </div>
  );
};

export default CacheMonitor;
