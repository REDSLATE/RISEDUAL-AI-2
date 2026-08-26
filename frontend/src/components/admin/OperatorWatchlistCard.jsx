import React, { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ClipboardPaste, Loader2, RefreshCw, Trash2, XCircle } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api/admin/operator-watchlist`;

/**
 * OperatorWatchlistCard — paste research text, preview the picks Alpha
 * extracted, commit selected ones, and see live coverage against
 * Alpha's own predictions. Never injects synthetic signals — pure
 * observational lens over ``predictions`` filtered to the watchlist.
 */
const OperatorWatchlistCard = () => {
  const [text, setText]         = useState('');
  const [preview, setPreview]   = useState(null);        // {picks:[...]}
  const [selected, setSelected] = useState({});          // {SYMBOL: bool}
  const [ttl, setTtl]           = useState('session');
  const [conviction, setConv]   = useState('watch');
  const [busy, setBusy]         = useState(false);
  const [active, setActive]     = useState({ items: [], count: 0, covered: 0, gaps: 0 });
  const [err, setErr]           = useState(null);

  const loadActive = useCallback(async () => {
    try {
      const r = await authFetch(API);
      if (r.ok) setActive(await r.json());
    } catch (e) { setErr(e.message); }
  }, []);

  useEffect(() => { loadActive(); }, [loadActive]);

  const doPreview = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await authFetch(`${API}/parse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setPreview(j);
      // Auto-select non-penny picks per parser default.
      const s = {};
      (j.picks || []).forEach(p => { s[p.symbol] = !!p.auto_checked; });
      setSelected(s);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const doCommit = async () => {
    const picks = (preview?.picks || []).filter(p => selected[p.symbol]);
    if (!picks.length) return;
    setBusy(true); setErr(null);
    try {
      const r = await authFetch(API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ picks, ttl, conviction }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setPreview(null);
      setText('');
      await loadActive();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (symbol) => {
    try {
      await authFetch(`${API}/${symbol}`, { method: 'DELETE' });
      await loadActive();
    } catch (e) { setErr(e.message); }
  };

  const nSelected = Object.values(selected).filter(Boolean).length;

  return (
    <div data-testid="operator-watchlist-card" className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
            <ClipboardPaste className="w-4 h-4 text-cyan-400" /> Feed Alpha your picks
          </h3>
          <div className="text-xs text-zinc-500">
            Paste rankings, research notes, anything. Alpha extracts tickers.
            Never overrides Alpha&apos;s judgment — regime + gates still apply.
          </div>
        </div>
        <button
          data-testid="operator-watchlist-refresh"
          onClick={loadActive}
          className="text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200"
        >
          <RefreshCw className="w-3 h-3 inline" />
        </button>
      </div>

      {err && (
        <div data-testid="operator-watchlist-error" className="rounded border border-red-700/60 bg-red-900/20 p-2 text-xs text-red-300">
          {err}
        </div>
      )}

      {/* Paste box */}
      <div>
        <textarea
          data-testid="operator-watchlist-paste-textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste your morning rankings, research notes, or a plain list…"
          className="w-full h-24 rounded border border-zinc-700 bg-zinc-950 text-zinc-100 p-2 text-xs font-mono resize-none focus:outline-none focus:border-cyan-500"
        />
        <div className="mt-2 flex items-center gap-2">
          <button
            data-testid="operator-watchlist-preview-btn"
            onClick={doPreview}
            disabled={!text.trim() || busy}
            className="text-xs px-3 py-1.5 rounded bg-cyan-600 hover:bg-cyan-500 text-white disabled:opacity-50 flex items-center gap-1"
          >
            {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
            Preview picks
          </button>
          {text && (
            <button
              data-testid="operator-watchlist-clear-btn"
              onClick={() => { setText(''); setPreview(null); }}
              className="text-xs px-2 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300"
            >Clear</button>
          )}
        </div>
      </div>

      {/* Preview */}
      {preview && (preview.picks || []).length > 0 && (
        <div data-testid="operator-watchlist-preview" className="rounded border border-zinc-700 bg-zinc-950/60 p-3 space-y-2">
          <div className="text-xs text-zinc-400">
            Detected {preview.picks.length} — pennies unchecked by default.
          </div>
          <div className="max-h-64 overflow-y-auto space-y-1">
            {preview.picks.map((p) => (
              <label
                key={p.symbol}
                data-testid={`operator-watchlist-preview-row-${p.symbol}`}
                className="flex items-start gap-2 text-xs text-zinc-200 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={!!selected[p.symbol]}
                  onChange={(e) => setSelected({ ...selected, [p.symbol]: e.target.checked })}
                  data-testid={`operator-watchlist-preview-check-${p.symbol}`}
                  className="mt-0.5"
                />
                <div className="flex-1">
                  <span className="font-mono font-semibold">{p.symbol}</span>
                  {p.is_penny && <span className="ml-1 text-[10px] px-1 rounded bg-amber-500/20 text-amber-300">penny</span>}
                  {p.trigger != null && (
                    <span className="ml-2 text-zinc-400">trigger ${p.trigger}</span>
                  )}
                  {p.invalidation != null && p.invalidation !== p.trigger && (
                    <span className="ml-2 text-zinc-500">· stop ${p.invalidation}</span>
                  )}
                </div>
              </label>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <select
              value={ttl} onChange={(e) => setTtl(e.target.value)}
              data-testid="operator-watchlist-ttl-select"
              className="text-xs rounded bg-zinc-800 border border-zinc-700 text-zinc-200 px-2 py-1"
            >
              <option value="session">End of session</option>
              <option value="day">24h</option>
              <option value="week">1 week</option>
            </select>
            <select
              value={conviction} onChange={(e) => setConv(e.target.value)}
              data-testid="operator-watchlist-conviction-select"
              className="text-xs rounded bg-zinc-800 border border-zinc-700 text-zinc-200 px-2 py-1"
            >
              <option value="watch">Watchlist only</option>
              <option value="med">Medium conviction</option>
              <option value="high">High conviction</option>
            </select>
            <button
              onClick={doCommit}
              disabled={busy || nSelected === 0}
              data-testid="operator-watchlist-commit-btn"
              className="text-xs px-3 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50"
            >Add {nSelected} to watchlist</button>
          </div>
        </div>
      )}

      {/* Active watchlist */}
      <div>
        <div className="flex items-center justify-between text-xs text-zinc-400 mb-1">
          <span>
            Active watchlist · <span className="text-zinc-200">{active.count}</span> ·{' '}
            <span className="text-emerald-400">{active.covered} scored</span> ·{' '}
            <span className="text-amber-400">{active.gaps} gaps</span>
          </span>
        </div>
        {(active.items || []).length === 0 ? (
          <div className="text-xs text-zinc-500 italic">No active picks. Paste some rankings above.</div>
        ) : (
          <div className="space-y-1">
            {active.items.map((it) => (
              <div
                key={it.symbol}
                data-testid={`operator-watchlist-active-row-${it.symbol}`}
                className="flex items-center justify-between rounded border border-zinc-800 bg-zinc-950/40 px-2 py-1.5 text-xs"
              >
                <div className="flex items-center gap-2">
                  {it.has_prediction ? (
                    <CheckCircle2 className="w-3 h-3 text-emerald-400" title="Alpha has scored this" />
                  ) : (
                    <XCircle className="w-3 h-3 text-amber-400" title="No fresh prediction — Alpha hasn't scored this yet" />
                  )}
                  <span className="font-mono font-semibold text-zinc-100">{it.symbol}</span>
                  {it.trigger != null && (
                    <span className="text-zinc-500">trigger ${it.trigger}</span>
                  )}
                  {it.has_prediction && (
                    <span className="text-zinc-400">
                      · {it.prediction?.direction} · conf {(it.prediction?.confidence ?? 0).toFixed(2)}
                    </span>
                  )}
                </div>
                <button
                  data-testid={`operator-watchlist-delete-${it.symbol}`}
                  onClick={() => doDelete(it.symbol)}
                  className="text-zinc-500 hover:text-red-400"
                  title="Remove from watchlist"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default OperatorWatchlistCard;
