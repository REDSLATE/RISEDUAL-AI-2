/**
 * AgentActivityFeed — live narrative of what the autonomous
 * pieces of RISEDUAL are doing.
 *
 * Polls `/api/agent/activity` every 10s and appends new events
 * to the top with a subtle animation. The feed turns the
 * agent's work from a black box into a visible coworker narrating
 * its decisions in plain English.
 *
 * Polling vs SSE decision: polling. SSE is more "live-feel" but
 * adds connection-pool cost and reconnect complexity. A 10s poll
 * is effectively real-time for human perception and scales
 * trivially. The endpoint supports `?since=` for incremental
 * fetch, so polling is cheap.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, Wifi, WifiOff, RotateCcw, Loader2, ChevronDown, HelpCircle } from 'lucide-react';
import { toast } from 'sonner';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';

const API_BASE = getApiBase();
const API = `${API_BASE}/api/agent/activity`;
const REPLAY_API = `${API_BASE}/api/admin/alerts/replay`;
const POLL_INTERVAL_MS = 10_000;

// Filter chip definitions. `test` receives the raw event type and
// returns true when the event belongs in the category. Kept as
// startsWith so new alert_*/paper_trade_*/retrain_* variants fold
// into the existing chips with zero wiring.
const FILTERS = [
  { key: 'all', label: 'All', test: () => true },
  { key: 'trades', label: 'Trades', test: (t) => t?.startsWith('paper_trade_') || t?.startsWith('prediction_') },
  { key: 'alerts', label: 'Alerts', test: (t) => t?.startsWith('alert_') || t?.startsWith('kill_switch_') },
  { key: 'ml', label: 'ML', test: (t) => t?.startsWith('retrain_') },
];

// Mirror of backend FAILURE_MODES in services/post_mortem_service.py.
// Kept small + inline so admins see the teaching copy directly in
// the alert_reserved drilldown without another round trip.
const FAILURE_MODE_DESCRIPTIONS = {
  TECH_FAKEOUT: 'Indicators were bullish but price reversed immediately (stop-loss hunt).',
  MACRO_SHOCK: 'Unexpected news/data (CPI, Fed, etc.) invalidated the setup.',
  LIQUIDITY_GAP: 'Low volume caused slippage or erratic price spikes.',
  REGIME_SHIFT: 'Market shifted from trending to range-bound unexpectedly.',
  UNKNOWN: 'Price moved against prediction without clear technical or news trigger.',
};

// Severity → classnames. Kept local to this component since no
// other surface renders agent-activity tone.
const SEVERITY_STYLES = {
  success: {
    bar: 'bg-emerald-400',
    badge: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
  },
  warn: {
    bar: 'bg-amber-400',
    badge: 'text-amber-300 bg-amber-500/10 border-amber-500/30',
  },
  error: {
    bar: 'bg-red-400',
    badge: 'text-red-300 bg-red-500/10 border-red-500/30',
  },
  info: {
    bar: 'bg-[#3DE8D9]',
    badge: 'text-[#3DE8D9] bg-[#3DE8D9]/10 border-[#3DE8D9]/30',
  },
};

function formatRelativeTime(isoTimestamp) {
  // Relative time label. Absolute clock appears on hover via title.
  // Kept tiny to avoid pulling a date library.
  const now = Date.now();
  const then = Date.parse(isoTimestamp);
  if (Number.isNaN(then)) return 'just now';
  const diffSec = Math.max(0, (now - then) / 1000);
  if (diffSec < 60) return `${Math.floor(diffSec)}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

// Turns a structured `why` list into a one-line narrative used
// above the per-feature breakdown. Design goal: convert
// "momentum_14 · bullish · 0.42" rows into a sentence someone
// unfamiliar with the feature names can still parse. Keep tight
// — this is a teaching hook, not a dissertation.
function summarizeWhy(why, eventType) {
  if (!why || why.length === 0) return null;
  const bearish = why.filter((w) => w.impact < 0).map((w) => w.feature);
  const bullish = why.filter((w) => w.impact >= 0).map((w) => w.feature);
  if (bearish.length === 0 && bullish.length === 0) return null;
  // Phrasing flips based on event type — a skip reads as "what
  // held back"; an open reads as "what drove in".
  const isSkip = eventType === 'paper_trade_skip';
  if (isSkip && bearish.length > 0 && bullish.length > 0) {
    return `Skipped because ${bearish.join(', ')} outweighed ${bullish.join(', ')}`;
  }
  if (isSkip && bearish.length > 0) {
    return `Held back by ${bearish.join(', ')}`;
  }
  if (isSkip && bullish.length > 0) {
    return `Bullish signals (${bullish.join(', ')}) didn't clear conviction threshold`;
  }
  if (bullish.length > 0) {
    return `Driven by ${bullish.join(', ')}`;
  }
  return `Contrarian take — ${bearish.join(', ')} pushed this trade`;
}

const WhyBlock = ({ why, eventType }) => {
  if (!why || why.length === 0) return null;
  const summary = summarizeWhy(why, eventType);
  return (
    <div
      className="mt-2 pt-2 border-t border-slate-700/40"
      data-testid="agent-activity-why"
    >
      {summary && (
        <p className="text-[10px] italic text-slate-400 leading-snug">
          {summary}
        </p>
      )}
      <ul className="mt-1 space-y-0.5">
        {why.map((w, i) => (
          <li
            key={`${w.feature}-${i}`}
            className="text-[9px] text-slate-500 flex items-center justify-between gap-2 font-mono"
          >
            <span className="truncate">{w.feature}</span>
            <span
              className={`shrink-0 tabular-nums ${
                w.direction === 'bullish' ? 'text-emerald-400' : 'text-red-400'
              }`}
            >
              {w.direction === 'bullish' ? '↑' : '↓'}{' '}
              {w.impact >= 0 ? '+' : ''}
              {w.impact.toFixed(3)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
};

// Drilldown for `alert_reserved` rows — shows the top toxic
// predictions with their confidence, failure code, and the human
// description. Turns the feed from "something fired" into
// "here's what went wrong and why".
//
// Two-tier data strategy:
//   1. Inline (instant)  — `spike_details` already on the event
//      metadata. Carries symbol/confidence/failure_code/date.
//   2. Enriched (on open) — fetch `/api/admin/alerts/why/{id}` to
//      add regime + feature-level drivers pulled from the most
//      recent features_snapshots row per ticker. Falls back to
//      inline data cleanly if the fetch fails.
const SpikeDetailsBlock = ({ spikes, alertId }) => {
  const [enriched, setEnriched] = useState(null);
  const [loadingEnriched, setLoadingEnriched] = useState(false);

  useEffect(() => {
    if (!alertId) return;
    let cancelled = false;
    (async () => {
      setLoadingEnriched(true);
      try {
        const res = await authFetch(
          `${API_BASE}/api/admin/alerts/why/${encodeURIComponent(alertId)}`,
        );
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setEnriched(data.items || []);
      } catch (e) {
        logger.warn('alert why fetch failed', e);
      } finally {
        if (!cancelled) setLoadingEnriched(false);
      }
    })();
    return () => { cancelled = true; };
  }, [alertId]);

  // Merge: enriched rows (if available) win by symbol, else inline.
  const rows = (() => {
    if (!enriched) return spikes || [];
    const bySym = new Map();
    (spikes || []).forEach((s) => { if (s.symbol) bySym.set(s.symbol, s); });
    enriched.forEach((e) => {
      const base = bySym.get(e.symbol) || {};
      bySym.set(e.symbol, { ...base, ...e });
    });
    return Array.from(bySym.values());
  })();

  if (!rows || rows.length === 0) {
    return (
      <div className="mt-2 pt-2 border-t border-slate-700/40">
        <p className="text-[10px] italic text-slate-500">
          No per-prediction detail recorded for this alert.
        </p>
      </div>
    );
  }
  return (
    <div
      className="mt-2 pt-2 border-t border-slate-700/40"
      data-testid="agent-activity-spike-details"
    >
      <p className="text-[10px] italic text-slate-400 leading-snug mb-1.5">
        Top {rows.length} highest-confidence miss{rows.length === 1 ? '' : 'es'}
        {loadingEnriched ? ' · loading drivers…' : ''}
        {' — model was most sure and most wrong here:'}
      </p>
      <ul className="space-y-1.5">
        {rows.map((s, i) => {
          const confPct =
            typeof s.confidence === 'number'
              ? s.confidence > 1
                ? s.confidence
                : s.confidence * 100
              : null;
          const code = s.failure_code || 'UNKNOWN';
          const desc = FAILURE_MODE_DESCRIPTIONS[code] || FAILURE_MODE_DESCRIPTIONS.UNKNOWN;
          return (
            <li
              key={`${s.symbol || '?'}-${i}`}
              className="rounded bg-slate-900/50 border border-slate-700/40 px-2 py-1.5"
              data-testid={`agent-activity-spike-row-${i}`}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-mono font-bold text-white">
                  {s.symbol || '?'}
                </span>
                {confPct != null && (
                  <span className="text-[9px] font-semibold text-amber-300 tabular-nums">
                    {confPct.toFixed(0)}% confidence
                  </span>
                )}
                <span className="text-[9px] font-semibold text-red-300 px-1.5 rounded bg-red-500/10 border border-red-500/30">
                  {code}
                </span>
                {s.regime && (
                  <span className="text-[9px] font-semibold text-cyan-300 px-1.5 rounded bg-cyan-500/10 border border-cyan-500/30">
                    {s.regime}
                  </span>
                )}
                {s.date && (
                  <span className="text-[9px] text-slate-500 tabular-nums">{s.date}</span>
                )}
              </div>
              <p className="text-[10px] text-slate-400 mt-0.5 leading-snug">{desc}</p>
              {s.drivers && s.drivers.length > 0 && (
                <ul
                  className="mt-1.5 space-y-0.5 text-[10px] text-yellow-300 list-disc pl-4 marker:text-yellow-500/70"
                  data-testid={`agent-activity-spike-drivers-${i}`}
                >
                  {s.drivers.map((d, j) => (
                    <li key={`${i}-driver-${j}-${String(d).slice(0, 24)}`} className="leading-snug">{d}</li>
                  ))}
                </ul>
              )}
              {s.shap_top && s.shap_top.length > 0 && (
                <div
                  className="mt-1.5 pt-1.5 border-t border-slate-700/30"
                  data-testid={`agent-activity-spike-shap-${i}`}
                >
                  <p className="text-[9px] uppercase tracking-wider text-slate-500 mb-0.5 font-semibold">
                    SHAP (model attribution)
                  </p>
                  <ul className="space-y-0.5">
                    {s.shap_top.map((c, j) => {
                      const pushed = c.contribution >= 0 ? 'up' : 'down';
                      return (
                        <li key={`${i}-shap-${c.feature || j}`} className="text-[10px] font-mono tabular-nums flex items-center gap-2">
                          <span className="text-slate-300">{c.feature}</span>
                          <span className={pushed === 'up' ? 'text-emerald-300' : 'text-rose-300'}>
                            {c.contribution >= 0 ? '+' : ''}{c.contribution.toFixed(3)}
                          </span>
                          <span className="text-slate-500 text-[9px]">pushed {pushed}</span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

// ── Adaptation "why" block ─────────────────────────────────────
// Rendered inline under `retrain_adaptation_applied` events.
// Each adaptation chip gets a lightweight "Why?" button that
// lazy-fetches `/api/admin/adaptations/why/{id}` the first time
// it's opened, caches the response, and toggles the explanation
// on subsequent clicks. Non-blocking — a fetch failure leaves the
// chip rendered, just without the narrative underneath.
const AdaptationBlock = ({ metadata }) => {
  const [openId, setOpenId] = useState(null);
  const [cache, setCache] = useState({}); // adaptation_id -> payload
  const [loadingId, setLoadingId] = useState(null);

  const adaptations = metadata?.adaptations || [];
  const mwBefore = metadata?.mean_weight_before;
  const mwAfter = metadata?.mean_weight_after;
  const mwDelta = metadata?.mean_weight_delta;
  const pi = metadata?.projected_impact || {};

  const loadWhy = useCallback(async (aid) => {
    if (!aid) return;
    if (openId === aid) { setOpenId(null); return; }
    if (cache[aid]) { setOpenId(aid); return; }
    setLoadingId(aid);
    try {
      const res = await authFetch(
        `${API_BASE}/api/admin/adaptations/why/${encodeURIComponent(aid)}`,
      );
      if (res.ok) {
        const d = await res.json();
        setCache((c) => ({ ...c, [aid]: d }));
        setOpenId(aid);
      }
    } catch (e) {
      logger.warn('adaptation why fetch failed', e);
    } finally {
      setLoadingId(null);
    }
  }, [cache, openId]);

  if (adaptations.length === 0) return null;

  return (
    <div
      className="mt-2 pt-2 border-t border-slate-700/40"
      data-testid="agent-activity-adaptation-block"
    >
      {mwBefore !== null && mwBefore !== undefined && mwAfter !== null && mwAfter !== undefined && (
        <p
          className="text-[10px] font-mono text-slate-400 leading-snug mb-1.5 tabular-nums"
          data-testid="agent-activity-adaptation-weights"
        >
          Mean weight <span className="text-slate-200">{mwBefore.toFixed(3)}</span>{' '}
          <span className="text-slate-500">→</span>{' '}
          <span className="text-slate-200">{mwAfter.toFixed(3)}</span>
          {typeof mwDelta === 'number' && (
            <span className={`ml-1 ${mwDelta < 0 ? 'text-rose-300' : 'text-emerald-300'}`}>
              (Δ {mwDelta >= 0 ? '+' : ''}{mwDelta.toFixed(3)})
            </span>
          )}
        </p>
      )}
      <ul className="space-y-1.5" data-testid="agent-activity-adaptation-chips">
        {adaptations.map((a, i) => {
          const id = a.adaptation_id;
          const pct = Math.round((1 - (a.factor ?? 1.0)) * 100);
          const isOpen = openId === id;
          const explain = cache[id];
          const loading = loadingId === id;
          return (
            <li
              key={id || i}
              className="flex flex-col gap-1"
              data-testid={`agent-activity-adaptation-row-${i}`}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="px-2 py-0.5 rounded bg-amber-900/30 border border-amber-600/30 text-amber-200 text-[10px] font-mono tabular-nums">
                  {a.metric}
                  {a.direction && a.direction !== 'ANY' ? ` / ${a.direction}` : ''}
                  {' '}×{(a.factor ?? 1.0).toFixed(2)}
                  {' '}({a.rows_matched ?? 0} rows)
                  {pct > 0 ? ` · −${pct}% weight` : ''}
                </span>
                {a.lift != null && (
                  <span className="text-[9px] text-purple-300 font-mono tabular-nums">
                    {Number(a.lift).toFixed(2)}× baseline
                  </span>
                )}
                {id && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); loadWhy(id); }}
                    className="text-[10px] font-semibold text-[#3DE8D9] hover:text-[#7AEEE0] disabled:opacity-50 inline-flex items-center gap-1"
                    disabled={loading}
                    data-testid={`agent-activity-adaptation-why-${i}`}
                  >
                    {loading ? (
                      <><Loader2 className="w-3 h-3 animate-spin" /> Loading…</>
                    ) : isOpen ? (
                      <><ChevronDown className="w-3 h-3" /> Hide</>
                    ) : (
                      <><HelpCircle className="w-3 h-3" /> Why?</>
                    )}
                  </button>
                )}
              </div>
              {isOpen && explain && (
                <div
                  className="ml-1 pl-2 border-l border-slate-600/60 text-[10px] text-slate-300 leading-snug"
                  data-testid={`agent-activity-adaptation-why-body-${i}`}
                >
                  <p className="text-slate-200">{explain.explanation}</p>
                  <p className="text-slate-500 mt-0.5">
                    {explain.projected_effect}
                    {typeof explain.evidence_count === 'number' && (
                      <>{' · '}{explain.evidence_count} toxic event{explain.evidence_count === 1 ? '' : 's'}</>
                    )}
                    {typeof explain.bucket_rate === 'number' && typeof explain.global_rate === 'number' && (
                      <>{' · '}bucket {(explain.bucket_rate * 100).toFixed(1)}% vs baseline {(explain.global_rate * 100).toFixed(1)}%</>
                    )}
                  </p>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {pi?.sample_size ? (
        <p
          className="text-[9px] text-slate-500 mt-1.5 leading-snug italic"
          data-testid="agent-activity-adaptation-projected"
        >
          Projected: {pi.matched_predictions ?? 0}/{pi.sample_size} recent predictions fall in an adapted bucket
          {typeof pi.coverage === 'number' && (
            <>{' '}({Math.round(pi.coverage * 100)}% coverage)</>
          )}
        </p>
      ) : null}
    </div>
  );
};



const EventRow = ({ event, isNew, onReplay, replayingId }) => {
  const [drillOpen, setDrillOpen] = useState(false);
  const style = SEVERITY_STYLES[event.severity] || SEVERITY_STYLES.info;
  const why = event.metadata?.why;
  const isDelivery = event.type === 'alert_delivery';
  const isReserved = event.type === 'alert_reserved';
  const isAdaptation = event.type === 'retrain_adaptation_applied';
  const failedCount = isDelivery ? (event.metadata?.failed?.length || 0) : 0;
  const canReplay = isDelivery && failedCount > 0 && event.metadata?.alert_id;
  const isReplaying = replayingId && replayingId === event.metadata?.alert_id;
  const spikes = isReserved ? (event.metadata?.spike_details || []) : [];
  const canDrill = isReserved && spikes.length > 0;
  return (
    <div
      className={`relative flex items-start gap-3 py-2.5 px-3 border-l-2 ${
        isNew ? 'animate-fade-in-slide' : ''
      }`}
      style={{ borderLeftColor: 'transparent' }}
      data-testid={`agent-activity-row-${event.type}`}
    >
      {/* Colored accent bar — runs full height of the row */}
      <span
        className={`absolute left-0 top-0 bottom-0 w-0.5 ${style.bar}`}
        aria-hidden
      />
      <span className="text-base shrink-0 mt-0.5" aria-hidden>
        {event.icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p
            className="text-xs text-white leading-snug font-medium truncate"
            title={event.detail || event.title}
          >
            {event.title}
          </p>
          {event.symbol && (
            <span
              className={`shrink-0 text-[9px] font-bold px-1.5 py-0.5 rounded border ${style.badge}`}
              data-testid="agent-activity-symbol"
            >
              {event.symbol}
            </span>
          )}
        </div>
        {event.detail && (
          <p className="text-[10px] text-slate-400 mt-0.5 leading-snug line-clamp-2">
            {event.detail}
          </p>
        )}
        <p
          className="text-[9px] text-slate-500 mt-0.5 tabular-nums"
          title={event.timestamp}
        >
          {formatRelativeTime(event.timestamp)}
        </p>
        {canReplay && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onReplay(event.metadata.alert_id); }}
            disabled={isReplaying}
            className="mt-1.5 inline-flex items-center gap-1 text-[10px] font-semibold text-amber-300 hover:text-amber-200 disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid={`agent-activity-replay-${event.metadata.alert_id?.slice(0, 12)}`}
          >
            {isReplaying ? (
              <><Loader2 className="w-3 h-3 animate-spin" /> Replaying…</>
            ) : (
              <><RotateCcw className="w-3 h-3" /> Replay failed ({failedCount})</>
            )}
          </button>
        )}
        {canDrill && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setDrillOpen((v) => !v); }}
            className="mt-1.5 inline-flex items-center gap-1 text-[10px] font-semibold text-[#3DE8D9] hover:text-[#7AEEE0]"
            data-testid={`agent-activity-drill-${event.metadata.alert_id?.slice(0, 12)}`}
          >
            {drillOpen ? (
              <><ChevronDown className="w-3 h-3" /> Hide why</>
            ) : (
              <><HelpCircle className="w-3 h-3" /> Why did this fire? ({spikes.length})</>
            )}
          </button>
        )}
        {canDrill && drillOpen && (
          <SpikeDetailsBlock
            spikes={spikes}
            alertId={event.metadata?.alert_id}
          />
        )}
        {isAdaptation && <AdaptationBlock metadata={event.metadata} />}
        <WhyBlock why={why} eventType={event.type} />
      </div>
    </div>
  );
};

const AgentActivityFeed = () => {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(true);
  const [newIds, setNewIds] = useState(new Set());
  const [filterKey, setFilterKey] = useState('all');
  const [replayingId, setReplayingId] = useState(null);
  // Track last seen timestamp for incremental polling. Using a ref
  // because we don't want fetch() to re-create on every update.
  const lastTsRef = useRef(null);
  // Bump a tick every 30s so relative-time labels refresh without
  // requiring an API call.
  const [, setTick] = useState(0);

  const fetchEvents = useCallback(async (incremental = true) => {
    try {
      const params = new URLSearchParams({ limit: '50' });
      if (incremental && lastTsRef.current) {
        params.set('since', lastTsRef.current);
      }
      const res = await authFetch(`${API}?${params.toString()}`);
      if (!res.ok) {
        setConnected(false);
        return;
      }
      const data = await res.json();
      setConnected(true);
      const fresh = data.events || [];
      if (fresh.length === 0) return;
      if (incremental) {
        // Prepend new events, dedupe by event_id, cap to 100 rows.
        setEvents((prev) => {
          const seen = new Set(prev.map((e) => e.event_id));
          const onlyNew = fresh.filter((e) => !seen.has(e.event_id));
          if (onlyNew.length === 0) return prev;
          setNewIds(new Set(onlyNew.map((e) => e.event_id)));
          // Clear the "new" highlight after the animation finishes.
          setTimeout(() => setNewIds(new Set()), 1500);
          return [...onlyNew, ...prev].slice(0, 100);
        });
      } else {
        setEvents(fresh);
      }
      // Advance the incremental cursor to the newest timestamp.
      const newest = fresh[0]?.timestamp;
      if (newest) lastTsRef.current = newest;
    } catch (e) {
      logger.warn('agent activity fetch failed', e);
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    fetchEvents(false); // initial load, non-incremental
    const pollId = setInterval(() => fetchEvents(true), POLL_INTERVAL_MS);
    const tickId = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => {
      clearInterval(pollId);
      clearInterval(tickId);
    };
  }, [fetchEvents]);

  const handleReplay = useCallback(async (alertId) => {
    if (!alertId) return;
    setReplayingId(alertId);
    try {
      const res = await authFetch(
        `${REPLAY_API}?alert_id=${encodeURIComponent(alertId)}`,
        { method: 'POST' },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
      if (data.status === 'no_failed_recipients') {
        toast.info('Nothing to replay — all recipients already delivered.');
      } else {
        const okN = (data.replayed || []).length;
        const failN = (data.still_failed || []).length;
        if (failN === 0) {
          toast.success(`Replay delivered to ${okN} recipient${okN === 1 ? '' : 's'} · attempt #${data.delivery_attempts}`);
        } else {
          toast.warning(`Replayed ${okN}; ${failN} still failing · attempt #${data.delivery_attempts}`);
        }
      }
      // Optimistically refetch the feed so the new alert_replay event
      // appears without waiting for the 10s poll.
      fetchEvents(true);
    } catch (e) {
      logger.warn('agent activity replay failed', e);
      toast.error(`Replay failed: ${e.message}`);
    } finally {
      setReplayingId(null);
    }
  }, [fetchEvents]);

  const filter = FILTERS.find((f) => f.key === filterKey) || FILTERS[0];
  const visibleEvents = useMemo(
    () => events.filter((e) => filter.test(e.type)),
    [events, filter],
  );
  const hasEvents = visibleEvents.length > 0;
  const headerConnectionDot = useMemo(() => {
    return connected ? (
      <span
        className="flex items-center gap-1 text-[10px] text-emerald-400"
        data-testid="agent-activity-connected"
      >
        <Wifi className="w-3 h-3" /> live
      </span>
    ) : (
      <span
        className="flex items-center gap-1 text-[10px] text-amber-400"
        data-testid="agent-activity-disconnected"
      >
        <WifiOff className="w-3 h-3" /> reconnecting
      </span>
    );
  }, [connected]);

  return (
    <div
      className="bg-slate-900/70 border border-slate-700/60 rounded-xl overflow-hidden"
      data-testid="agent-activity-feed"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/60">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-[#3DE8D9]" />
          <h3 className="text-sm font-bold text-white">Agent Activity</h3>
        </div>
        {headerConnectionDot}
      </div>
      <div
        className="max-h-[420px] overflow-y-auto divide-y divide-slate-700/40"
        data-testid="agent-activity-list"
      >
        {hasEvents ? (
          events.map((e) => (
            <EventRow
              key={e.event_id}
              event={e}
              isNew={newIds.has(e.event_id)}
            />
          ))
        ) : (
          <div className="px-4 py-8 text-center" data-testid="agent-activity-empty">
            <Activity className="w-6 h-6 text-slate-600 mx-auto mb-2" />
            <p className="text-xs text-slate-400">
              The agent will narrate its work here.
            </p>
            <p className="text-[10px] text-slate-500 mt-1">
              Scans, trades, resolutions, and model retrains all appear live.
            </p>
          </div>
        )}
      </div>
      <style>{`
        @keyframes fade-in-slide {
          from { opacity: 0; transform: translateY(-4px); background-color: rgba(61, 232, 217, 0.08); }
          to   { opacity: 1; transform: translateY(0);     background-color: transparent; }
        }
        .animate-fade-in-slide {
          animation: fade-in-slide 1.5s ease-out;
        }
      `}</style>
    </div>
  );
};

export default AgentActivityFeed;
