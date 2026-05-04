import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  RefreshCw, ArrowDownCircle, ArrowUpCircle, Eye, Settings2, AlertOctagon,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import AdversarialCoresChip from './AdversarialCoresChip';

const API = `${getApiBase()}/api`;

/**
 * Top Actions panel — operator's prioritized action queue surfaced
 * from `/api/terminal/top-actions`. Polls every 30s while mounted.
 *
 * Card kinds:
 *   - EXIT          (red, alert-octagon)   — sovereign reversed
 *                                            against an open position
 *   - ENTER         (emerald, arrow-up)    — high-conviction LONG/SHORT
 *   - MANAGE_POSITION (slate, settings)    — open position visibility
 *   - WATCH         (amber, eye)           — sub-threshold signal
 */
const KIND_CONFIG = {
  EXIT: {
    label: 'EXIT',
    Icon: AlertOctagon,
    border: 'border-rose-500/60',
    bg: 'bg-rose-500/10',
    text: 'text-rose-300',
    badge: 'bg-rose-500/20 text-rose-200',
  },
  ENTER: {
    label: 'ENTER',
    Icon: ArrowUpCircle,
    border: 'border-emerald-500/40',
    bg: 'bg-emerald-500/5',
    text: 'text-emerald-300',
    badge: 'bg-emerald-500/20 text-emerald-200',
  },
  MANAGE_POSITION: {
    label: 'MANAGE',
    Icon: Settings2,
    border: 'border-slate-600/50',
    bg: 'bg-slate-700/20',
    text: 'text-slate-300',
    badge: 'bg-slate-700/40 text-slate-200',
  },
  WATCH: {
    label: 'WATCH',
    Icon: Eye,
    border: 'border-amber-500/30',
    bg: 'bg-amber-500/5',
    text: 'text-amber-200',
    badge: 'bg-amber-500/20 text-amber-200',
  },
};

const ActionCard = ({ action }) => {
  const cfg = KIND_CONFIG[action.kind] || KIND_CONFIG.WATCH;
  const { Icon } = cfg;

  const sideIcon =
    action.action === 'SHORT' || action.action === 'SELL'
      ? <ArrowDownCircle className="h-3.5 w-3.5 text-rose-400" />
      : <ArrowUpCircle className="h-3.5 w-3.5 text-emerald-400" />;

  return (
    <div
      data-testid={`top-action-card-${action.symbol}`}
      className={`rounded-xl border ${cfg.border} ${cfg.bg} p-3 transition-colors hover:bg-opacity-80`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-slate-500 text-xs tabular-nums w-5 shrink-0">
            #{action.rank}
          </span>
          <Icon className={`h-4 w-4 shrink-0 ${cfg.text}`} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span
                className="font-semibold text-white tabular-nums tracking-wide"
                data-testid={`top-action-symbol-${action.symbol}`}
              >
                {action.symbol}
              </span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${cfg.badge}`}>
                {cfg.label}
              </span>
              {action.shadow && (
                <span className="text-[9px] uppercase tracking-wider text-slate-500">
                  shadow
                </span>
              )}
            </div>
            <div className="flex items-center gap-1 text-xs text-slate-400 mt-0.5">
              {sideIcon}
              <span>{action.action}</span>
              <span className="text-slate-600">·</span>
              <span className="tabular-nums">
                conf {(action.conviction?.score ?? 0).toFixed(2)}
              </span>
              {action.vetoes_count > 0 && (
                <>
                  <span className="text-slate-600">·</span>
                  <span className="text-rose-400">
                    {action.vetoes_count} veto
                  </span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-sm font-semibold text-white tabular-nums">
            {(action.priority_score * 100).toFixed(0)}
          </div>
          <div className="text-[10px] text-slate-500 uppercase tracking-wider">
            score
          </div>
        </div>
      </div>

      <div className="mt-2 text-xs text-slate-400">{action.reason}</div>

      {action.correlation_note && (
        <div
          className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1 text-[11px] text-amber-300"
          data-testid={`top-action-correlation-note-${action.symbol}`}
        >
          {action.correlation_note}
        </div>
      )}
    </div>
  );
};

const TotalsRow = ({ totals }) => {
  const items = [
    { key: 'exit', label: 'EXIT', color: 'text-rose-300' },
    { key: 'enter', label: 'ENTER', color: 'text-emerald-300' },
    { key: 'manage', label: 'MANAGE', color: 'text-slate-300' },
    { key: 'watch', label: 'WATCH', color: 'text-amber-200' },
  ];
  return (
    <div className="flex items-center gap-4 text-xs">
      {items.map((it) => (
        <div key={it.key} className="flex items-center gap-1.5">
          <span className={`${it.color} font-semibold tabular-nums`}>
            {totals?.[it.key] ?? 0}
          </span>
          <span className="text-slate-500 uppercase tracking-wider text-[10px]">
            {it.label}
          </span>
        </div>
      ))}
    </div>
  );
};

const TerminalTopActions = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastPoll, setLastPoll] = useState(null);
  const [streamConnected, setStreamConnected] = useState(false);
  const esRef = useRef(null);

  const fetchActions = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/terminal/top-actions?limit=10`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
      setLastPoll(new Date());
    } catch (e) {
      setError(e.message || 'fetch failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Primary path — SSE subscription. Pushes fresh snapshots only
    // when the underlying data structurally changes. Falls back to
    // 30s polling if the browser or proxy blocks EventSource.
    fetchActions();
    const streamUrl = `${API}/terminal/top-actions/stream?limit=10`;
    try {
      const es = new EventSource(streamUrl, { withCredentials: true });
      esRef.current = es;
      es.addEventListener('snapshot', (evt) => {
        try {
          const payload = JSON.parse(evt.data);
          setData(payload);
          setError(null);
          setLastPoll(new Date());
          setStreamConnected(true);
          setLoading(false);
        } catch (_) { /* ignore malformed frame */ }
      });
      es.addEventListener('heartbeat', () => {
        setStreamConnected(true);
        setLastPoll(new Date());
      });
      es.onerror = () => {
        setStreamConnected(false);
        // EventSource auto-reconnects; no manual retry needed.
      };
    } catch (_) {
      // EventSource unsupported — fall through to polling.
      setStreamConnected(false);
    }

    // Fallback polling always runs at 60s so if SSE drops silently,
    // the operator still sees fresh data within the minute.
    const id = setInterval(fetchActions, 60_000);
    return () => {
      clearInterval(id);
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [fetchActions]);

  return (
    <div className="p-4 sm:p-6 space-y-4" data-testid="terminal-top-actions">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-white">
            Top Actions
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Sovereign-driven action queue · last 24h · max 5 ENTER per tick
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={`text-[10px] tabular-nums ${streamConnected ? 'text-emerald-400' : 'text-slate-500'}`}
            data-testid="terminal-top-actions-stream-status"
            title={streamConnected ? 'SSE stream connected' : 'polling fallback'}
          >
            {streamConnected ? '● live' : '○ poll'}
            {lastPoll && (
              <> · {Math.max(0, Math.floor((Date.now() - lastPoll.getTime()) / 1000))}s</>
            )}
          </span>
          <button
            onClick={fetchActions}
            disabled={loading}
            data-testid="terminal-top-actions-refresh"
            className="text-slate-400 hover:text-white transition-colors p-1.5 rounded-md hover:bg-slate-800"
            title="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      <AdversarialCoresChip />

      {error && (
        <div
          className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-300"
          data-testid="terminal-top-actions-error"
        >
          {error}
        </div>
      )}

      {data && (
        <>
          <div className="rounded-lg border border-slate-700/50 bg-slate-900/40 p-3">
            <TotalsRow totals={data.totals} />
          </div>

          {data.actions?.length === 0 ? (
            <div
              className="rounded-xl border border-slate-700/60 bg-slate-900/30 p-6 text-center text-sm text-slate-500"
              data-testid="terminal-top-actions-empty"
            >
              No actions in the lookback window. Sovereign is quiet —
              this is normal during market closes or low-conviction
              regimes.
            </div>
          ) : (
            <div className="space-y-2">
              {data.actions?.map((action) => (
                <ActionCard
                  key={`${action.symbol}-${action.decision_id || 'orphan'}`}
                  action={action}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default TerminalTopActions;
