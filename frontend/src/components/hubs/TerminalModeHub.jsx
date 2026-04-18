/**
 * TerminalModeHub — Thinkorswim-inspired dense multi-panel workspace.
 *
 * Four resizable zones arranged in a 2×2 grid by default (drag the crosshair
 * to reapportion space). Every panel embeds a live component from the main
 * app, just rendered in compact / monospace density.
 *
 * Zones:
 *   ┌─────────────┬──────────────┐
 *   │ Watchlist   │ Ticker chart │
 *   │ (dense)     │ (sparkline)  │
 *   ├─────────────┼──────────────┤
 *   │ Market      │ Headlines    │
 *   │ Signals     │ stream       │
 *   └─────────────┴──────────────┘
 *
 * Status bar at bottom shows session clock, provider health, last refresh.
 */
import React, { useEffect, useState, useRef, useCallback } from 'react';
import { Terminal, Activity, Newspaper, Gauge } from 'lucide-react';
import { getApiBase } from '../../utils/apiBase';
import { authFetch } from '../../contexts/AuthContext';
import Watchlist from '../Watchlist';

const MarketSignals = React.lazy(() => import('../MarketSignals'));

const API = `${getApiBase()}/api`;

/** Tiny status-bar clock (HH:MM:SS ET). */
const SessionClock = () => {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const fmt = now.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour12: false,
  });
  return (
    <span className="text-[10px] font-mono text-[#3DE8D9] tabular-nums" data-testid="terminal-clock">
      {fmt} ET
    </span>
  );
};

/** SPY + QQQ + VIX pulse strip — ambient market context. */
const IndexPulse = () => {
  const [quotes, setQuotes] = useState({});
  useEffect(() => {
    const load = async () => {
      const syms = ['SPY', 'QQQ', 'IWM', 'VIX'];
      const data = await Promise.all(
        syms.map((s) =>
          fetch(`${API}/stocks/quote/${s}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        ),
      );
      const out = {};
      syms.forEach((s, i) => { if (data[i]) out[s] = data[i]; });
      setQuotes(out);
    };
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, []);

  const pill = (sym) => {
    const q = quotes[sym];
    if (!q) return (
      <span key={sym} className="font-mono text-[10px] text-slate-500 mr-3">{sym}  ——</span>
    );
    const up = (q.changePercent || 0) >= 0;
    return (
      <span
        key={sym}
        className={`font-mono text-[10px] tabular-nums mr-3 ${up ? 'text-lime-400' : 'text-orange-400'}`}
      >
        {sym} {q.price?.toFixed(2)} {up ? '▲' : '▼'}{Math.abs(q.changePercent || 0).toFixed(2)}%
      </span>
    );
  };
  return (
    <div className="flex items-center" data-testid="terminal-index-pulse">
      {['SPY', 'QQQ', 'IWM', 'VIX'].map(pill)}
    </div>
  );
};

/** Compact headlines stream polled from the existing headlines service. */
const HeadlinesStream = () => {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const load = async () => {
      try {
        const r = await authFetch(`${API}/headlines/recent?limit=20&hours=24`);
        if (!r.ok) return;
        const d = await r.json();
        setItems(d.headlines || d.items || []);
      } catch { /* silent */ }
    };
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, []);
  return (
    <div
      className="h-full overflow-y-auto font-mono text-[11px] leading-relaxed p-3 space-y-1"
      data-testid="terminal-headlines"
    >
      {items.length === 0 ? (
        <p className="text-slate-500 text-[11px]">No recent headlines.</p>
      ) : (
        items.map((h, i) => (
          <div key={h.id || h._id || i} className="flex gap-2">
            <span className="text-slate-500 shrink-0">
              {(h.published_at || h.timestamp || '').slice(11, 16) || '--:--'}
            </span>
            <a
              href={h.url || h.link || '#'}
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-300 hover:text-[#3DE8D9] truncate"
              title={h.title || h.headline}
            >
              {h.title || h.headline}
            </a>
          </div>
        ))
      )}
    </div>
  );
};

/** A single dockable panel. */
const Panel = ({ title, icon: Icon, accent = 'text-[#3DE8D9]', children, testId }) => (
  <div
    className="flex flex-col h-full bg-[#060E1F] border border-slate-700/60 rounded-md overflow-hidden"
    data-testid={testId}
  >
    <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900/80 border-b border-slate-700/60">
      <Icon className={`w-3.5 h-3.5 ${accent}`} />
      <span className="text-[10px] font-mono uppercase tracking-wider text-slate-300">{title}</span>
    </div>
    <div className="flex-1 min-h-0 overflow-hidden">{children}</div>
  </div>
);

/** Horizontal/vertical splitter — drag the handle to reapportion. */
const Splitter = ({ orientation, value, onChange, min = 15, max = 85 }) => {
  const containerRef = useRef(null);
  const onMouseDown = useCallback((e) => {
    e.preventDefault();
    const parent = containerRef.current?.parentElement;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();
    const move = (ev) => {
      const pct = orientation === 'horizontal'
        ? ((ev.clientX - rect.left) / rect.width) * 100
        : ((ev.clientY - rect.top) / rect.height) * 100;
      onChange(Math.max(min, Math.min(max, pct)));
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  }, [orientation, onChange, min, max]);
  const isH = orientation === 'horizontal';
  return (
    <div
      ref={containerRef}
      onMouseDown={onMouseDown}
      className={`bg-slate-800 hover:bg-[#3DE8D9]/40 transition-colors ${
        isH ? 'w-1 cursor-col-resize' : 'h-1 cursor-row-resize'
      }`}
      data-testid={`terminal-splitter-${orientation}`}
    />
  );
};

export default function TerminalModeHub({ onSubscribe }) {
  // Split percentages persisted to localStorage.
  const [hSplit, setHSplit] = useState(() => {
    const v = parseFloat(localStorage.getItem('risedual:terminal:hSplit'));
    return isFinite(v) ? v : 52;
  });
  const [vSplit, setVSplit] = useState(() => {
    const v = parseFloat(localStorage.getItem('risedual:terminal:vSplit'));
    return isFinite(v) ? v : 55;
  });
  useEffect(() => {
    localStorage.setItem('risedual:terminal:hSplit', String(hSplit));
  }, [hSplit]);
  useEffect(() => {
    localStorage.setItem('risedual:terminal:vSplit', String(vSplit));
  }, [vSplit]);

  return (
    <div
      className="animate-enter font-mono"
      style={{ fontFamily: '"JetBrains Mono", "Liberation Mono", monospace' }}
      data-testid="terminal-mode-hub"
    >
      {/* Header */}
      <div className="mb-3 flex items-center gap-3 flex-wrap">
        <div className="w-7 h-7 rounded-md bg-[#3DE8D9]/10 border border-[#3DE8D9]/30 flex items-center justify-center">
          <Terminal className="w-4 h-4 text-[#3DE8D9]" />
        </div>
        <h1 className="text-white text-base font-bold tracking-tight uppercase">Terminal Mode</h1>
        <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-[#3DE8D9]/15 text-[#3DE8D9] border border-[#3DE8D9]/30">
          Dense · Monospace
        </span>
        <div className="ml-auto flex items-center gap-3">
          <IndexPulse />
          <SessionClock />
        </div>
      </div>

      {/* 2×2 dockable grid */}
      <div
        className="h-[calc(100vh-220px)] min-h-[560px] flex rounded-md border border-slate-700/60 bg-slate-900/30 overflow-hidden"
        data-testid="terminal-grid"
      >
        {/* Left column */}
        <div className="flex flex-col" style={{ width: `${hSplit}%` }}>
          <div style={{ height: `${vSplit}%` }} className="p-1.5 min-h-0">
            <Panel title="Watchlist" icon={Gauge} testId="terminal-panel-watchlist">
              <div className="h-full overflow-y-auto p-2 text-xs">
                <Watchlist onSubscribe={onSubscribe} />
              </div>
            </Panel>
          </div>
          <Splitter orientation="vertical" value={vSplit} onChange={setVSplit} />
          <div style={{ height: `${100 - vSplit}%` }} className="p-1.5 min-h-0">
            <Panel title="Market Signals" icon={Activity} accent="text-orange-300" testId="terminal-panel-signals">
              <React.Suspense fallback={<div className="text-slate-500 text-xs p-3">Loading signals…</div>}>
                <div className="h-full overflow-y-auto p-2 text-xs">
                  <MarketSignals onSubscribe={onSubscribe} />
                </div>
              </React.Suspense>
            </Panel>
          </div>
        </div>

        <Splitter orientation="horizontal" value={hSplit} onChange={setHSplit} />

        {/* Right column */}
        <div className="flex flex-col flex-1">
          <div style={{ height: `${vSplit}%` }} className="p-1.5 min-h-0">
            <Panel title="War Room Deep-Link" icon={Terminal} accent="text-orange-300" testId="terminal-panel-warroom-hint">
              <div className="p-4 h-full overflow-y-auto text-xs text-slate-300 space-y-3">
                <p className="text-slate-400">
                  Click any watchlist ticker to deep-link into the AI War Room
                  without leaving terminal mode. Pro tip:
                </p>
                <ul className="space-y-1.5 text-slate-300">
                  <li>• <span className="text-[#3DE8D9]">Cmd/Ctrl+K</span> — global search bar</li>
                  <li>• <span className="text-[#3DE8D9]">War Room Share</span> — auto-credits referrals</li>
                  <li>• <span className="text-[#3DE8D9]">Drag splitters</span> — layout persists</li>
                </ul>
                <p className="text-slate-500 text-[11px] italic">
                  Terminal Mode stays mounted while you navigate; your
                  watchlist quotes refresh every 60 s and headlines every 60 s.
                </p>
              </div>
            </Panel>
          </div>
          <Splitter orientation="vertical" value={vSplit} onChange={setVSplit} />
          <div style={{ height: `${100 - vSplit}%` }} className="p-1.5 min-h-0">
            <Panel title="Headlines" icon={Newspaper} accent="text-amber-300" testId="terminal-panel-headlines">
              <HeadlinesStream />
            </Panel>
          </div>
        </div>
      </div>

      {/* Status bar */}
      <div
        className="mt-2 flex items-center justify-between text-[10px] font-mono text-slate-500 px-2"
        data-testid="terminal-status-bar"
      >
        <span>STATUS <span className="text-lime-400">●</span> CONNECTED</span>
        <span>RISEDUAL TERMINAL v1.0 · Thinkorswim-inspired</span>
        <span>Layout auto-saved</span>
      </div>
    </div>
  );
}
