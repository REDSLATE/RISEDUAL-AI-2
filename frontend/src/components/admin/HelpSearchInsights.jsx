import React, { useEffect, useState, useCallback } from 'react';
import { Search, TrendingUp, AlertCircle, Zap, RefreshCw, Mail } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { toast } from '../ui/sonner';

const API = `${getApiBase()}/api`;

export default function HelpSearchInsights() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);
  const [sendingDigest, setSendingDigest] = useState(false);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      const r = await authFetch(`${API}/analytics/help-search/stats?days=${days}&limit=20`);
      if (r.ok) setStats(await r.json());
    } finally {
      setLoading(false);
    }
  }, [days]);

  const sendDigest = useCallback(async () => {
    setSendingDigest(true);
    try {
      const r = await authFetch(`${API}/analytics/help-search/send-digest`, { method: 'POST' });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || 'Request failed');
      if (data.skipped) {
        toast.info(`Digest skipped: ${data.reason}${data.zero_events !== undefined ? ` (${data.zero_events} zero-result events)` : ''}`);
      } else {
        toast.success(`Digest sent to ${data.sent} admin${data.sent === 1 ? '' : 's'}${data.errors ? ` (${data.errors} errors)` : ''}`);
      }
    } catch (e) {
      toast.error(`Send failed: ${e.message}`);
    } finally {
      setSendingDigest(false);
    }
  }, []);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  if (loading && !stats) {
    return <div className="p-6 text-slate-400 text-sm">Loading search insights…</div>;
  }
  if (!stats) {
    return <div className="p-6 text-slate-400 text-sm">No data yet.</div>;
  }

  const zeroPct = (stats.zero_result_rate * 100).toFixed(1);

  return (
    <div className="p-4 sm:p-6 space-y-5" data-testid="help-search-insights">
      {/* Header + window toggle */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-[#3DE8D9]/10 border border-[#3DE8D9]/30 flex items-center justify-center">
            <Search className="w-3.5 h-3.5 text-[#3DE8D9]" />
          </div>
          <div>
            <h3 className="text-white font-bold text-sm">Help Search Insights</h3>
            <p className="text-slate-500 text-[11px]">What users can&rsquo;t find — the feature-gap radar.</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {[7, 30, 90].map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-2 py-1 text-[11px] font-semibold rounded-md border transition-colors ${
                days === d
                  ? 'bg-[#3DE8D9]/15 text-[#3DE8D9] border-[#3DE8D9]/40'
                  : 'bg-slate-800/60 text-slate-400 border-slate-700/40 hover:text-white'
              }`}
            >
              {d}d
            </button>
          ))}
          <button
            onClick={fetchStats}
            className="ml-1 p-1.5 rounded-md bg-slate-800/60 text-slate-400 hover:text-white border border-slate-700/40"
            aria-label="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={sendDigest}
            disabled={sendingDigest}
            className="ml-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#3DE8D9]/15 text-[#3DE8D9] border border-[#3DE8D9]/40 text-[11px] font-semibold hover:bg-[#3DE8D9]/25 transition-colors disabled:opacity-50"
            data-testid="send-help-digest-btn"
            title="Send the 7-day zero-result summary to all admin/owner emails right now"
          >
            <Mail className={`w-3.5 h-3.5 ${sendingDigest ? 'animate-pulse' : ''}`} />
            {sendingDigest ? 'Sending…' : 'Email digest'}
          </button>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-xl border border-slate-700/40 bg-slate-900/40 p-3">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
            <TrendingUp className="w-3 h-3" /> Total
          </div>
          <div className="text-white text-xl font-bold">{stats.total_events}</div>
          <div className="text-[10px] text-slate-500">searches · {stats.window_days}d</div>
        </div>
        <div className="rounded-xl border border-orange-500/30 bg-orange-500/5 p-3">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-orange-400 font-semibold mb-1">
            <AlertCircle className="w-3 h-3" /> Zero-Result
          </div>
          <div className="text-orange-300 text-xl font-bold">{stats.zero_result_events}</div>
          <div className="text-[10px] text-orange-500/70">{zeroPct}% of queries</div>
        </div>
        <div className="rounded-xl border border-slate-700/40 bg-slate-900/40 p-3">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
            <Zap className="w-3 h-3" /> Gap Signal
          </div>
          <div className="text-white text-xl font-bold">
            {zeroPct > 20 ? 'High' : zeroPct > 10 ? 'Medium' : 'Low'}
          </div>
          <div className="text-[10px] text-slate-500">above 20% = ship docs/features</div>
        </div>
      </div>

      {/* Top zero-result queries */}
      <div>
        <h4 className="text-white text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 text-orange-400" /> Top Zero-Result Queries
        </h4>
        {stats.zero_result_top.length === 0 ? (
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/30 p-6 text-center">
            <p className="text-slate-400 text-sm">No zero-result searches in this window. 🎉</p>
            <p className="text-slate-500 text-[11px] mt-1">Every help query found at least one hit.</p>
          </div>
        ) : (
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/30 overflow-hidden">
            <table className="w-full">
              <thead className="bg-slate-900/60">
                <tr className="text-[10px] text-slate-500 uppercase tracking-wider">
                  <th className="text-left px-3 py-2 font-semibold">Query</th>
                  <th className="text-right px-3 py-2 font-semibold">Count</th>
                  <th className="text-left px-3 py-2 font-semibold hidden sm:table-cell">Context</th>
                  <th className="text-right px-3 py-2 font-semibold hidden sm:table-cell">Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {stats.zero_result_top.map((r) => (
                  <tr key={r.q} className="border-t border-slate-800/70 hover:bg-slate-800/30">
                    <td className="px-3 py-2 text-white text-sm font-medium">{r.q}</td>
                    <td className="px-3 py-2 text-right text-orange-300 text-sm font-bold tabular-nums">{r.count}</td>
                    <td className="px-3 py-2 text-slate-400 text-xs hidden sm:table-cell">
                      {r.hubs.length ? r.hubs.join(', ') : <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-3 py-2 text-right text-slate-500 text-[11px] hidden sm:table-cell">
                      {r.last_seen ? new Date(r.last_seen).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Top queries overall */}
      <div>
        <h4 className="text-white text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-2">
          <TrendingUp className="w-3.5 h-3.5 text-[#3DE8D9]" /> Top Queries (Any Result)
        </h4>
        {stats.top_queries.length === 0 ? (
          <div className="text-slate-500 text-xs py-4">No queries yet.</div>
        ) : (
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/30 overflow-hidden">
            <table className="w-full">
              <thead className="bg-slate-900/60">
                <tr className="text-[10px] text-slate-500 uppercase tracking-wider">
                  <th className="text-left px-3 py-2 font-semibold">Query</th>
                  <th className="text-right px-3 py-2 font-semibold">Count</th>
                  <th className="text-right px-3 py-2 font-semibold">Avg Results</th>
                </tr>
              </thead>
              <tbody>
                {stats.top_queries.map((r) => (
                  <tr key={r.q} className="border-t border-slate-800/70 hover:bg-slate-800/30">
                    <td className="px-3 py-2 text-white text-sm font-medium">{r.q}</td>
                    <td className="px-3 py-2 text-right text-[#3DE8D9] text-sm font-bold tabular-nums">{r.count}</td>
                    <td className="px-3 py-2 text-right text-slate-400 text-xs tabular-nums">{r.avg_results}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
