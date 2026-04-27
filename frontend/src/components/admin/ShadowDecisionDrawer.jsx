import React, { useEffect, useState, useCallback } from 'react';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * Shadow Decision Drawer — clickable timeline of shadow observations
 * for a single bot/symbol pair.
 *
 * Renders inline (not modal) so it reads as an expansion of the
 * trade row. Closes by setting `symbol={null}` on the parent.
 *
 * Each row:
 *   timestamp · active→shadow actions · dissent flag · score (✓/✗/pending)
 *
 * Pending rows (lookahead window not yet elapsed) show a "scoring…"
 * tag with a slate dot. Settled rows show ✓ (shadow was right) or
 * ✗ (shadow was wrong) plus the $ delta.
 */
export default function ShadowDecisionDrawer({ botId, symbol, onClose, onlyDissents = false }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [filterDissents, setFilterDissents] = useState(onlyDissents);

  const load = useCallback(async () => {
    if (!botId && !symbol) return;
    try {
      const params = new URLSearchParams({ limit: '50' });
      if (botId) params.set('bot_id', botId);
      if (symbol) params.set('symbol', symbol);
      if (filterDissents) params.set('only_dissents', 'true');

      const res = await fetch(`${API}/api/admin/shadow/decisions?${params}`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [botId, symbol, filterDissents]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <div
      className="rounded-xl border border-slate-700 bg-slate-950/80 p-4 mt-2"
      data-testid="shadow-decision-drawer"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="text-slate-200 text-sm font-semibold uppercase tracking-wider">
          Shadow Timeline — {symbol || botId || 'all'}
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500 flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={filterDissents}
              onChange={(e) => setFilterDissents(e.target.checked)}
              data-testid="shadow-drawer-dissent-filter"
            />
            Dissents only
          </label>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-300 text-xs"
            data-testid="shadow-drawer-close"
          >
            Close ✕
          </button>
        </div>
      </div>

      {/* Body */}
      {error ? (
        <div className="text-rose-300 text-xs">Drawer unavailable: {error}</div>
      ) : !data ? (
        <div className="text-slate-500 text-xs">Loading…</div>
      ) : data.items.length === 0 ? (
        <div className="text-slate-500 text-xs">
          No shadow decisions yet for this scope. Once the bot fires
          its next cycle with a shadow engine enabled, rows will
          appear here within seconds.
        </div>
      ) : (
        <DecisionTable items={data.items} />
      )}
    </div>
  );
}


const DecisionTable = ({ items }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-xs" data-testid="shadow-drawer-table">
      <thead>
        <tr className="text-slate-500 uppercase tracking-wider text-[10px] border-b border-slate-800">
          <th className="text-left py-1.5 pr-3">Time</th>
          <th className="text-left py-1.5 pr-3">Symbol</th>
          <th className="text-left py-1.5 pr-3">Active</th>
          <th className="text-left py-1.5 pr-3">Shadow</th>
          <th className="text-left py-1.5 pr-3">Phase</th>
          <th className="text-right py-1.5 pr-3">Δ $</th>
          <th className="text-right py-1.5">Verdict</th>
        </tr>
      </thead>
      <tbody>
        {items.map((row) => (
          <DecisionRow key={row.decision_id} row={row} />
        ))}
      </tbody>
    </table>
  </div>
);


const DecisionRow = ({ row }) => {
  const tactical = row.tactical_score;
  const scored = !!tactical;
  const right = scored && tactical.shadow_was_right;
  const delta = tactical?.delta_usd ?? 0;

  const rowTone = row.is_dissent
    ? 'bg-amber-950/20 border-l-2 border-amber-500/40 pl-2'
    : '';

  return (
    <tr
      className={`border-b border-slate-900/60 ${rowTone}`}
      data-testid={`shadow-drawer-row-${row.decision_id}`}
    >
      <td className="py-1.5 pr-3 text-slate-400 font-mono text-[10px]">
        {fmtTime(row.ts)}
      </td>
      <td className="py-1.5 pr-3 text-slate-300">{row.symbol}</td>
      <td className="py-1.5 pr-3 text-slate-300">
        <ActionPill action={row.active_action} />
      </td>
      <td className="py-1.5 pr-3 text-slate-300">
        <ActionPill action={row.shadow_action} dissent={row.is_dissent} />
      </td>
      <td className="py-1.5 pr-3 text-slate-500">{row.decision_phase}</td>
      <td className="py-1.5 pr-3 text-right">
        {scored ? (
          <span className={delta > 0 ? 'text-emerald-300' : delta < 0 ? 'text-rose-300' : 'text-slate-500'}>
            {delta > 0 ? '+' : ''}${delta.toFixed(2)}
          </span>
        ) : (
          <span className="text-slate-600">—</span>
        )}
      </td>
      <td className="py-1.5 text-right">
        <VerdictPill scored={scored} right={right} isDissent={row.is_dissent} />
      </td>
    </tr>
  );
};


const ActionPill = ({ action, dissent = false }) => {
  const colors = {
    LONG: 'border-emerald-500/40 bg-emerald-950/40 text-emerald-300',
    SHORT: 'border-rose-500/40 bg-rose-950/40 text-rose-300',
    HOLD: 'border-slate-600 bg-slate-900 text-slate-400',
    CLOSE: 'border-amber-500/40 bg-amber-950/40 text-amber-300',
  }[action] || 'border-slate-700 bg-slate-900 text-slate-500';
  const ring = dissent ? 'ring-1 ring-amber-400/50' : '';
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider font-semibold border ${colors} ${ring}`}>
      {action}
    </span>
  );
};


const VerdictPill = ({ scored, right, isDissent }) => {
  if (!isDissent) {
    return <span className="text-slate-600 text-[10px]">agree</span>;
  }
  if (!scored) {
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider font-semibold border border-slate-700 bg-slate-900 text-slate-400">
        scoring…
      </span>
    );
  }
  return right ? (
    <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider font-semibold border border-emerald-500/40 bg-emerald-950/40 text-emerald-300">
      ✓ shadow
    </span>
  ) : (
    <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider font-semibold border border-rose-500/40 bg-rose-950/40 text-rose-300">
      ✗ shadow
    </span>
  );
};


const fmtTime = (iso) => {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
};
