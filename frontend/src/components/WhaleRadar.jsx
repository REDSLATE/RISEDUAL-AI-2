import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Radio, Zap, TrendingUp, TrendingDown, Minus, AlertTriangle, Waves, Activity } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Progress } from './ui/progress';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';
import InfoTooltip from './InfoTooltip';

const RADAR_TICKERS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK', 'SHIB'];
const MAX_EVENTS = 30;

const biasIcon = (bias) => {
  if (bias === 'INSTITUTIONAL_BID') return <TrendingUp className="w-3 h-3 text-lime-400" />;
  if (bias === 'INSTITUTIONAL_ASK') return <TrendingDown className="w-3 h-3 text-orange-400" />;
  return <Minus className="w-3 h-3 text-slate-500" />;
};

const biasColor = (bias) => {
  if (bias === 'INSTITUTIONAL_BID') return 'text-lime-400';
  if (bias === 'INSTITUTIONAL_ASK') return 'text-orange-400';
  return 'text-slate-400';
};

const getIntensityColor = (val) => {
  if (val > 80) return 'text-red-500 drop-shadow-[0_0_12px_rgba(239,68,68,0.7)]';
  if (val > 50) return 'text-orange-500 drop-shadow-[0_0_8px_rgba(249,115,22,0.5)]';
  return 'text-[#3DE8D9] drop-shadow-[0_0_8px_rgba(61,232,217,0.5)]';
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
      } catch (err) { logger.warn('WhaleRadar status parse error:', err); }
    });

    es.addEventListener('tick', (e) => {
      try {
        const d = JSON.parse(e.data);
        setTickers(prev => ({
          ...prev,
          [d.ticker]: { mid: d.mid, bias: d.bias, bid_pct: d.bid_pct, wall_count: d.wall_count, whale_count: d.whale_count, ts: d.ts },
        }));
      } catch (err) { logger.warn('WhaleRadar tick parse error:', err); }
    });

    es.addEventListener('whale', (e) => {
      try {
        const d = JSON.parse(e.data);
        setWhaleEvents(prev => [{
          ticker: d.ticker, mid: d.mid, walls: d.walls, ts: d.ts,
          id: `${d.ticker}-${Date.now()}`,
        }, ...prev].slice(0, MAX_EVENTS));
      } catch (err) { logger.warn('WhaleRadar whale parse error:', err); }
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

  // Compute aggregate flow intensity from all tickers
  const intensity = useMemo(() => {
    const active = tickerList.filter(t => t.wall_count > 0);
    if (active.length === 0) return 0;
    const totalWalls = active.reduce((s, t) => s + (t.wall_count || 0), 0);
    const totalWhales = active.reduce((s, t) => s + (t.whale_count || 0), 0);
    return Math.min(100, Math.round((totalWalls * 2) + (totalWhales * 15)));
  }, [tickerList]);

  return (
    <Card className="bg-[#111C30] border-slate-700/50 shadow-2xl overflow-hidden" data-testid="whale-radar">
      {/* Header */}
      <CardHeader className="border-b border-white/5 pb-3">
        <div className="flex justify-between items-center">
          <CardTitle className="text-sm font-black flex items-center gap-2 text-white tracking-tight">
            <Radio className="w-4 h-4 text-red-500 animate-pulse" />
            WHALE RADAR LIVE
            <InfoTooltip id="whale-radar" />
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-slate-500 text-[10px]">{RADAR_TICKERS.length} pairs</span>
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-mono uppercase border ${
              connected
                ? 'bg-[#3DE8D9]/10 border-[#3DE8D9]/20 text-[#3DE8D9]'
                : 'bg-red-500/10 border-red-500/20 text-red-400'
            }`} data-testid="radar-status">
              {connected ? 'SSE Active' : 'Connecting'}
            </span>
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-5 space-y-5 px-4 pb-4">
        {/* Intensity Gauge */}
        <div className="flex flex-col items-center justify-center space-y-1.5 py-3" data-testid="radar-intensity">
          <div className={`text-6xl font-black tabular-nums tracking-tighter ${getIntensityColor(intensity)}`}>
            {intensity}
          </div>
          <div className="text-[10px] uppercase font-bold tracking-widest text-slate-400">
            Current Flow Intensity
          </div>
          <Progress value={intensity} className="h-1.5 w-full mt-3" />
        </div>

        {/* Ticker Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-px bg-slate-700/40 rounded-lg overflow-hidden" data-testid="radar-grid">
          {tickerList.map(t => (
            <div key={t.ticker}
              className={`px-3 py-2.5 bg-[#0D1526] hover:bg-slate-700/30 transition-colors ${
                t.whale_count > 0 ? 'ring-1 ring-inset ring-yellow-500/30' : ''
              }`}
              data-testid={`radar-tile-${t.ticker}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-white text-xs font-bold">{t.ticker}</span>
                <div className="flex items-center gap-1">
                  {t.whale_count > 0 && (
                    <span className="text-yellow-300 text-[9px] font-bold flex items-center gap-0.5" data-testid={`whale-badge-${t.ticker}`}>
                      <Zap className="w-2.5 h-2.5" /> {t.whale_count}
                    </span>
                  )}
                  {biasIcon(t.bias)}
                </div>
              </div>
              <div className="text-slate-400 text-[10px] font-mono">
                {t.mid ? `$${t.mid.toLocaleString()}` : '---'}
              </div>
              {/* Pressure bar */}
              <div className="h-1 rounded-full overflow-hidden flex bg-slate-800 mt-1.5">
                <div className="bg-green-500/50 transition-all duration-700" style={{ width: `${t.bid_pct || 50}%` }} />
                <div className="bg-red-500/50 transition-all duration-700" style={{ width: `${100 - (t.bid_pct || 50)}%` }} />
              </div>
              <div className="flex justify-between mt-1 text-[9px]">
                <span className="text-slate-500">{t.wall_count || 0} walls</span>
                <span className={biasColor(t.bias)}>{t.bid_pct != null ? `${t.bid_pct.toFixed(0)}%` : '--'}</span>
              </div>
            </div>
          ))}
        </div>

        {/* Latest Activity / Whale Events */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
            <Waves className="w-3 h-3" /> Whale Detections
            {whaleEvents.length > 0 && (
              <span className="text-yellow-300 font-mono ml-1">{whaleEvents.length}</span>
            )}
          </div>
          {whaleEvents.length === 0 ? (
            <div className="text-center text-slate-500 text-xs py-4">Scanning for whale activity...</div>
          ) : (
            <div className="max-h-40 overflow-y-auto space-y-1">
              {whaleEvents.slice(0, 10).map(ev => (
                <div key={ev.id} className="flex items-center gap-2 text-[11px] p-2 bg-white/[0.03] rounded border-l-2 border-[#3DE8D9]/30">
                  <span className="font-mono text-[#3DE8D9] font-bold w-10">{ev.ticker}</span>
                  <span className="text-slate-300 font-mono">${ev.mid?.toLocaleString()}</span>
                  <div className="flex gap-1 flex-wrap flex-1">
                    {ev.walls?.slice(0, 3).map((w, idx) => (
                      <span key={`${ev.id}-${w.side}-${w.price}-${idx}`} className={`px-1.5 py-0.5 rounded text-[9px] border ${
                        w.side === 'bid'
                          ? 'bg-green-500/10 border-emerald-500/20 text-lime-400'
                          : 'bg-red-500/10 border-red-500/20 text-orange-400'
                      }`}>
                        ${w.price?.toLocaleString()} <span className="text-yellow-300">{w.intensity}</span>
                      </span>
                    ))}
                  </div>
                  <span className="text-slate-600 text-[9px] whitespace-nowrap">
                    {ev.ts ? new Date(ev.ts).toLocaleTimeString() : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

export default WhaleRadar;
