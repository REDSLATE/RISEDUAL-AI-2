import React, { useEffect, useState, useCallback } from 'react';
import { MessageSquare, MousePointerClick, Eye, TrendingUp, RefreshCw, Zap } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

export default function ChipAdoptionInsights() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      const r = await authFetch(`${API}/analytics/chip-events/stats?days=${days}&limit=15`);
      if (r.ok) setStats(await r.json());
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  if (loading && !stats) {
    return <div className="p-6 text-slate-400 text-sm">Loading chip adoption…</div>;
  }
  if (!stats) {
    return <div className="p-6 text-slate-400 text-sm">No data yet.</div>;
  }

  const ctrPct = (stats.ctr * 100).toFixed(1);
  const actionCtrPct = ((stats.action_ctr || 0) * 100).toFixed(1);
  // Decision gate signal for Level-2 investment: >=20% CTR = strong adoption.
  const signal = stats.ctr >= 0.20 ? 'High' : stats.ctr >= 0.10 ? 'Medium' : 'Low';
  const signalColor = signal === 'High'
    ? 'text-[#3DE8D9]'
    : signal === 'Medium'
      ? 'text-amber-400'
      : 'text-slate-400';

  return (
    <div className="p-4 sm:p-6 space-y-5" data-testid="chip-adoption-insights">
      {/* Header + window toggle */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-[#3DE8D9]/10 border border-[#3DE8D9]/30 flex items-center justify-center">
            <MessageSquare className="w-3.5 h-3.5 text-[#3DE8D9]" />
          </div>
          <div>
            <h3 className="text-white font-bold text-sm">Chat Chip Adoption</h3>
            <p className="text-slate-500 text-[11px]">Does Level-1 conversational UX justify Level-2 inline actions?</p>
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
              data-testid={`chip-window-${d}d`}
            >
              {d}d
            </button>
          ))}
          <button
            onClick={fetchStats}
            className="ml-1 p-1.5 rounded-md bg-slate-800/60 text-slate-400 hover:text-white border border-slate-700/40"
            aria-label="Refresh"
            data-testid="chip-refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
        <div className="rounded-xl border border-slate-700/40 bg-slate-900/40 p-3">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
            <Eye className="w-3 h-3" /> Shown
          </div>
          <div className="text-white text-xl font-bold tabular-nums" data-testid="chip-kpi-shown">{stats.shown}</div>
          <div className="text-[10px] text-slate-500">L1 chips · {stats.window_days}d</div>
        </div>
        <div className="rounded-xl border border-[#3DE8D9]/30 bg-[#3DE8D9]/5 p-3">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[#3DE8D9] font-semibold mb-1">
            <MousePointerClick className="w-3 h-3" /> Clicked
          </div>
          <div className="text-[#3DE8D9] text-xl font-bold tabular-nums" data-testid="chip-kpi-clicked">{stats.clicked}</div>
          <div className="text-[10px] text-[#3DE8D9]/70">L1 follow-ups</div>
        </div>
        <div className="rounded-xl border border-slate-700/40 bg-slate-900/40 p-3">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
            <TrendingUp className="w-3 h-3" /> L1 CTR
          </div>
          <div className="text-white text-xl font-bold tabular-nums" data-testid="chip-kpi-ctr">{ctrPct}%</div>
          <div className="text-[10px] text-slate-500">chip click-through</div>
        </div>
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-amber-400 font-semibold mb-1">
            <TrendingUp className="w-3 h-3" /> L2 CTR
          </div>
          <div className="text-amber-300 text-xl font-bold tabular-nums" data-testid="chip-kpi-action-ctr">{actionCtrPct}%</div>
          <div className="text-[10px] text-amber-500/70">
            deep-link clicks · {stats.action_clicked || 0}/{stats.action_shown || 0}
          </div>
        </div>
        <div className="rounded-xl border border-slate-700/40 bg-slate-900/40 p-3">
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
            <Zap className="w-3 h-3" /> Signal
          </div>
          <div className={`text-xl font-bold ${signalColor}`} data-testid="chip-kpi-signal">{signal}</div>
          <div className="text-[10px] text-slate-500">L1 &ge;20% = strong</div>
        </div>
      </div>

      {/* Top clicked chips */}
      <div>
        <h4 className="text-white text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-2">
          <MousePointerClick className="w-3.5 h-3.5 text-[#3DE8D9]" /> Top Clicked Chips
        </h4>
        {stats.top_clicked.length === 0 ? (
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/30 p-6 text-center">
            <p className="text-slate-400 text-sm">No chip clicks yet in this window.</p>
            <p className="text-slate-500 text-[11px] mt-1">
              Chips appear automatically after assistant replies &gt; 20 chars. Give it a few conversations to collect signal.
            </p>
          </div>
        ) : (
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/30 overflow-hidden">
            <table className="w-full">
              <thead className="bg-slate-900/60">
                <tr className="text-[10px] text-slate-500 uppercase tracking-wider">
                  <th className="text-left px-3 py-2 font-semibold">Chip</th>
                  <th className="text-right px-3 py-2 font-semibold">Clicks</th>
                </tr>
              </thead>
              <tbody>
                {stats.top_clicked.map((r, i) => (
                  <tr key={i} className="border-t border-slate-800/70 hover:bg-slate-800/30">
                    <td className="px-3 py-2 text-white text-sm font-medium">{r.chip}</td>
                    <td className="px-3 py-2 text-right text-[#3DE8D9] text-sm font-bold tabular-nums">{r.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {/* Top clicked Level-2 actions */}
      <div>
        <h4 className="text-white text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-2">
          <TrendingUp className="w-3.5 h-3.5 text-amber-400" /> Top Clicked Deep-Link Actions
        </h4>
        {(!stats.top_actions || stats.top_actions.length === 0) ? (
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/30 p-6 text-center">
            <p className="text-slate-400 text-sm">No deep-link clicks yet in this window.</p>
            <p className="text-slate-500 text-[11px] mt-1">
              Level-2 action buttons (e.g. &ldquo;Open AAPL Research&rdquo;) appear when the LLM detects a clear routing intent.
            </p>
          </div>
        ) : (
          <div className="rounded-xl border border-slate-700/40 bg-slate-900/30 overflow-hidden">
            <table className="w-full">
              <thead className="bg-slate-900/60">
                <tr className="text-[10px] text-slate-500 uppercase tracking-wider">
                  <th className="text-left px-3 py-2 font-semibold">Action</th>
                  <th className="text-right px-3 py-2 font-semibold">Clicks</th>
                </tr>
              </thead>
              <tbody>
                {stats.top_actions.map((r, i) => (
                  <tr key={i} className="border-t border-slate-800/70 hover:bg-slate-800/30">
                    <td className="px-3 py-2 text-white text-sm font-medium">{r.chip}</td>
                    <td className="px-3 py-2 text-right text-amber-300 text-sm font-bold tabular-nums">{r.count}</td>
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
