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
import { Activity, Wifi, WifiOff } from 'lucide-react';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';

const API = `${getApiBase()}/api/agent/activity`;
const POLL_INTERVAL_MS = 10_000;

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

const EventRow = ({ event, isNew }) => {
  const style = SEVERITY_STYLES[event.severity] || SEVERITY_STYLES.info;
  const why = event.metadata?.why;
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
        <WhyBlock why={why} eventType={event.type} />
      </div>
    </div>
  );
};

const AgentActivityFeed = () => {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(true);
  const [newIds, setNewIds] = useState(new Set());
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

  const hasEvents = events.length > 0;
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
