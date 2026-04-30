import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ShieldOff,
  RefreshCw,
  Download,
  TrendingUp,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { toast } from '../ui/sonner';

const API = `${getApiBase()}/api/admin/blocks-prevented/summary`;
const WINDOWS = [
  { label: 'Quarter', value: null },
  { label: '7d', value: 7 },
  { label: '30d', value: 30 },
  { label: '90d', value: 90 },
];

/**
 * BlocksPreventedCard — investor-facing summary of the IP contract's
 * defensive value. Reads from the `EXECUTION_REJECTED` blocks of the
 * proof chain (single source of truth, hash-verifiable counts).
 *
 * Hero card with one-click PNG export — operators screenshot it for
 * board / investor decks. The card's framing is intentional:
 *
 *   "X trades the IP refused this quarter — $Y in exposure prevented"
 *
 * with a per-patent attribution table so reviewers can see WHICH
 * gate did the blocking, an asset-class breakdown, and a weekly
 * sparkline of activity.
 */
const BlocksPreventedCard = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [windowDays, setWindowDays] = useState(null); // null = quarter
  const [exporting, setExporting] = useState(false);
  const cardRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = new URL(API);
      if (windowDays != null) url.searchParams.set('days', String(windowDays));
      const r = await authFetch(url.toString());
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(e.message || 'load_failed');
    } finally {
      setLoading(false);
    }
  }, [windowDays]);

  useEffect(() => {
    load();
  }, [load]);

  const exportPNG = async () => {
    if (!cardRef.current) return;
    setExporting(true);
    try {
      const html2canvas = (await import('html2canvas')).default;
      const canvas = await html2canvas(cardRef.current, {
        backgroundColor: '#0F172A',
        scale: 2,
      });
      const link = document.createElement('a');
      const date = new Date().toISOString().slice(0, 10);
      link.download = `risedual-blocks-prevented-${date}.png`;
      link.href = canvas.toDataURL('image/png');
      link.click();
      toast.success('Exported to PNG');
    } catch {
      toast.error('Export failed');
    } finally {
      setExporting(false);
    }
  };

  if (error) {
    return (
      <div
        className="p-3 rounded-lg bg-slate-800/40 border border-red-500/30 text-xs text-red-300"
        data-testid="blocks-prevented-error"
      >
        Blocks Prevented error: {error}
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="blocks-prevented-card">
      {/* Window selector + actions row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs">
          {WINDOWS.map((w) => (
            <button
              key={w.label}
              onClick={() => setWindowDays(w.value)}
              className={`px-2.5 py-1 rounded-md border text-[10px] font-medium transition-colors ${
                windowDays === w.value
                  ? 'bg-[#3DE8D9]/10 border-[#3DE8D9]/30 text-[#3DE8D9]'
                  : 'bg-slate-800/40 border-slate-700/40 text-slate-300 hover:border-slate-600/60'
              }`}
              data-testid={`blocks-prevented-window-${w.label.toLowerCase()}`}
            >
              {w.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-slate-700/40 hover:bg-slate-700/60 border border-slate-600/40 text-slate-200 text-[10px] font-medium transition-colors"
            data-testid="blocks-prevented-refresh"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={exportPNG}
            disabled={exporting || !data}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-[#3DE8D9] hover:bg-[#7AEEE0] disabled:opacity-40 text-slate-900 text-[10px] font-semibold transition-colors"
            data-testid="blocks-prevented-export"
          >
            <Download className="w-3 h-3" />
            {exporting ? 'Exporting…' : 'Export PNG'}
          </button>
        </div>
      </div>

      {/* The hero card — what gets exported */}
      <div
        ref={cardRef}
        className="p-5 rounded-2xl bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-700/40"
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <div className="flex items-center gap-2 text-[10px] text-slate-400 uppercase tracking-[0.2em]">
              <ShieldOff className="w-3 h-3 text-[#3DE8D9]" />
              Blocks Prevented
            </div>
            <div className="text-[10px] text-slate-500 mt-0.5">
              {data?.window?.label || '—'} · proof-chain verified
            </div>
          </div>
          <div className="text-right">
            <div className="text-[9px] text-slate-500 uppercase tracking-wider">
              RISEDUAL IP
            </div>
            <div className="text-[9px] text-[#3DE8D9] font-mono">
              Patent K · Aud · Auth · M · I
            </div>
          </div>
        </div>

        {/* Hero numbers */}
        <div className="grid grid-cols-2 gap-4 mb-5">
          <div data-testid="blocks-prevented-total">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider">
              Trades Refused
            </div>
            <div className="text-4xl font-bold font-mono text-white tabular-nums mt-1">
              {data?.total_blocked ?? '—'}
            </div>
          </div>
          <div data-testid="blocks-prevented-exposure">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider">
              Exposure Prevented
            </div>
            <div className="text-4xl font-bold font-mono text-[#3DE8D9] tabular-nums mt-1">
              ${formatUSD(data?.estimated_exposure_prevented_usd || 0)}
            </div>
          </div>
        </div>

        {/* Weekly sparkline */}
        {data?.weekly_series?.length > 0 && (
          <div className="mb-4">
            <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">
              Weekly Activity
            </div>
            <WeeklySparkline series={data.weekly_series} />
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* By reason / patent */}
          <div data-testid="blocks-prevented-by-reason">
            <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1.5">
              By Patent
            </div>
            {(data?.by_reason || []).length === 0 ? (
              <div className="text-[10px] text-slate-500 italic">
                No blocks in this window.
              </div>
            ) : (
              <div className="space-y-1">
                {data.by_reason.slice(0, 6).map((r) => (
                  <ReasonRow
                    key={r.reason}
                    label={r.label}
                    count={r.count}
                    total={data.total_blocked}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Top symbols + by asset class */}
          <div data-testid="blocks-prevented-by-asset">
            <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1.5">
              By Asset Class
            </div>
            <div className="space-y-1 mb-3">
              {(data?.by_asset_class || []).slice(0, 4).map((a) => (
                <div
                  key={a.asset_class}
                  className="flex items-center justify-between text-[11px]"
                >
                  <span className="text-slate-300 capitalize">{a.asset_class}</span>
                  <span className="text-slate-200 font-mono tabular-nums">
                    {a.count}
                  </span>
                </div>
              ))}
            </div>

            {(data?.top_symbols || []).length > 0 && (
              <>
                <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1.5">
                  Top Symbols
                </div>
                <div className="flex flex-wrap gap-1">
                  {data.top_symbols.slice(0, 8).map((s) => (
                    <span
                      key={s.symbol}
                      className="px-1.5 py-0.5 rounded bg-slate-800/60 border border-slate-700/40 text-[10px] text-slate-300 font-mono"
                    >
                      {s.symbol} <span className="text-slate-500">×{s.count}</span>
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        <div className="mt-4 pt-3 border-t border-slate-700/40 text-[9px] text-slate-500 flex items-baseline justify-between">
          <span>
            Generated {data?.generated_at ? new Date(data.generated_at).toLocaleString() : '—'}
          </span>
          <span className="font-mono">risedual.ai</span>
        </div>
      </div>
    </div>
  );
};

// ── Sub-components ────────────────────────────────────────────────────

const ReasonRow = ({ label, count, total }) => {
  const pct = total > 0 ? (count / total) * 100 : 0;
  return (
    <div className="text-[11px]">
      <div className="flex items-baseline justify-between mb-0.5">
        <span className="text-slate-300 truncate">{label}</span>
        <span className="text-slate-200 font-mono tabular-nums ml-2">
          {count} <span className="text-slate-500 text-[9px]">({pct.toFixed(0)}%)</span>
        </span>
      </div>
      <div className="h-1 rounded-full bg-slate-800/80 overflow-hidden">
        <div
          className="h-full bg-[#3DE8D9]/80 rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
};

const WeeklySparkline = ({ series }) => {
  const max = Math.max(1, ...series.map((s) => s.count));
  return (
    <div
      className="h-10 flex items-end gap-1"
      data-testid="blocks-prevented-sparkline"
    >
      {series.map((s) => {
        const height = (s.count / max) * 100;
        return (
          <div
            key={s.week_start}
            className="flex-1 flex flex-col justify-end"
            title={`${s.week_start}: ${s.count} blocks`}
          >
            <div
              className="w-full bg-[#3DE8D9]/70 rounded-t-sm hover:bg-[#3DE8D9] transition-all"
              style={{ height: s.count === 0 ? '2px' : `${Math.max(height, 8)}%` }}
            />
          </div>
        );
      })}
    </div>
  );
};

// Render large dollar values compactly: 12450 → "12.5k", 1340000 → "1.3M".
function formatUSD(n) {
  const v = Number(n) || 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 10_000) return `${(v / 1000).toFixed(1)}k`;
  return v.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

export default BlocksPreventedCard;
