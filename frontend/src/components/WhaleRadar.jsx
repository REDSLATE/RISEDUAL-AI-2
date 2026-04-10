import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Radio, Zap, TrendingUp, TrendingDown, Minus, AlertTriangle } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const RADAR_TICKERS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK', 'SHIB'];
const MAX_EVENTS = 30;

const biasIcon = (bias) => {
  if (bias === 'INSTITUTIONAL_BID') return <TrendingUp className="w-3 h-3 text-emerald-400" />;
  if (bias === 'INSTITUTIONAL_ASK') return <TrendingDown className="w-3 h-3 text-red-400" />;
  return <Minus className="w-3 h-3 text-slate-500" />;
};

const biasColor = (bias) => {
  if (bias === 'INSTITUTIONAL_BID') return 'text-emerald-400';
  if (bias === 'INSTITUTIONAL_ASK') return 'text-red-400';
  return 'text-slate-400';
};

const WhaleRadar = () => {
  const [connected, setConnected] = useState(false);
  const [tickers, setTickers] = useState({});
  const [whaleEvents, setWhaleEvents] = useState([]);
  const esRef = useRef(null);

  const sseUrl = `${getApiBase()}/api/stream/whale-radar`;

  const connect = useCallback(() => {
    const es = new EventSource(sseUrl);
    esRef.current = es;

    es.onopen = () => setConnected(true);

    es.addEventListener('radar_status', (e) => {
      try {
        const data = JSON.parse(e.data);
        const init = {};
        data.tickers?.forEach(t => { init[t] = { mid: null, bias: null, bid_pct: 50, wall_count: 0, whale_count: 0 }; });
        setTickers(init);
      } catch {}
    });

    es.addEventListener('tick', (e) => {
      try {
        const d = JSON.parse(e.data);
        setTickers(prev => ({
          ...prev,
          [d.ticker]: {
            mid: d.mid,
            bias: d.bias,
            bid_pct: d.bid_pct,
            wall_count: d.wall_count,
            whale_count: d.whale_count,
            ts: d.ts,
          },
        }));
      } catch {}
    });

    es.addEventListener('whale', (e) => {
      try {
        const d = JSON.parse(e.data);
        setWhaleEvents(prev => [{
          ticker: d.ticker,
          mid: d.mid,
          walls: d.walls,
          ts: d.ts,
          id: `${d.ticker}-${Date.now()}`,
        }, ...prev].slice(0, MAX_EVENTS));
      } catch {}
    });

    es.onerror = () => {
      setConnected(false);
      es.close();
      setTimeout(connect, 3000);
    };

    return es;
  }, [sseUrl]);

  useEffect(() => {
    const es = connect();
    return () => { es.close(); };
  }, [connect]);

  const tickerList = RADAR_TICKERS.map(t => ({ ticker: t, ...tickers[t] }));

  return (
    <div className="bg-slate-900/40 backdrop-blur-sm border border-slate-800/50 rounded-xl overflow-hidden" data-testid="whale-radar">
      {/* Header */}
      <div className="px-4 py-3 flex items-center justify-between border-b border-slate-800/30">
        <div className="flex items-center gap-2">
          <Radio className="w-4 h-4 text-yellow-400" />
          <h3 className="text-white text-sm font-semibold tracking-tight">Whale Radar</h3>
          <span className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${
            connected ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border-red-500/20 text-red-400'
          }`} data-testid="radar-status">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
            {connected ? 'SCANNING' : 'CONNECTING'}
          </span>
        </div>
        <span className="text-slate-600 text-[10px]">{RADAR_TICKERS.length} pairs monitored</span>
      </div>

      {/* Ticker Grid */}
      <div className="grid grid-cols-5 gap-px bg-slate-800/30 p-px" data-testid="radar-grid">
        {tickerList.map(t => (
          <div key={t.ticker}
            className={`px-3 py-2.5 bg-slate-900/80 hover:bg-slate-800/60 transition-colors ${
              t.whale_count > 0 ? 'ring-1 ring-inset ring-yellow-500/30' : ''
            }`}
            data-testid={`radar-tile-${t.ticker}`}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-white text-xs font-bold">{t.ticker}</span>
              <div className="flex items-center gap-1">
                {t.whale_count > 0 && (
                  <span className="text-yellow-400 text-[9px] font-bold flex items-center gap-0.5" data-testid={`whale-badge-${t.ticker}`}>
                    <Zap className="w-2.5 h-2.5" />
                    {t.whale_count}
                  </span>
                )}
                {biasIcon(t.bias)}
              </div>
            </div>
            <div className="text-slate-400 text-[10px] font-mono">
              {t.mid ? `$${t.mid.toLocaleString()}` : '---'}
            </div>
            {/* Mini pressure bar */}
            <div className="h-1 rounded-full overflow-hidden flex bg-slate-800 mt-1.5">
              <div className="bg-emerald-500/50 transition-all duration-700" style={{ width: `${t.bid_pct || 50}%` }} />
              <div className="bg-red-500/50 transition-all duration-700" style={{ width: `${100 - (t.bid_pct || 50)}%` }} />
            </div>
            <div className="flex justify-between mt-1 text-[9px]">
              <span className="text-slate-600">{t.wall_count || 0} walls</span>
              <span className={biasColor(t.bias)}>
                {t.bid_pct != null ? `${t.bid_pct.toFixed(0)}%` : '--'}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Whale Events Feed */}
      {whaleEvents.length > 0 && (
        <div className="border-t border-slate-800/30">
          <div className="px-4 py-2 flex items-center gap-2">
            <AlertTriangle className="w-3 h-3 text-yellow-400" />
            <span className="text-[10px] text-slate-500 uppercase tracking-wider">Whale Detections</span>
            <span className="text-[10px] text-yellow-400 font-mono">{whaleEvents.length}</span>
          </div>
          <div className="max-h-36 overflow-y-auto px-4 pb-3 space-y-1">
            {whaleEvents.slice(0, 10).map(ev => (
              <div key={ev.id} className="flex items-center gap-2 text-[10px] py-1 border-b border-slate-800/20 last:border-0">
                <span className="text-white font-bold w-10">{ev.ticker}</span>
                <span className="text-slate-500 font-mono">${ev.mid?.toLocaleString()}</span>
                <div className="flex gap-1 flex-wrap flex-1">
                  {ev.walls?.slice(0, 3).map((w, i) => (
                    <span key={i} className={`px-1.5 py-0.5 rounded border ${
                      w.side === 'bid'
                        ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                        : 'bg-red-500/10 border-red-500/20 text-red-400'
                    }`}>
                      ${w.price?.toLocaleString()} <span className="text-yellow-400">{w.intensity}</span>
                    </span>
                  ))}
                </div>
                <span className="text-slate-700 text-[9px]">
                  {ev.ts ? new Date(ev.ts).toLocaleTimeString() : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default WhaleRadar;
