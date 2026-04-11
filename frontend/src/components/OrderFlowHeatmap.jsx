import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Activity, Zap, ArrowUpDown, AlertTriangle } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const MAX_COLS = 60; // 60 seconds of history

const intensityToColor = (intensity, side) => {
  // Minimum floor of 0.35 so every data cell is clearly visible
  const floor = 0.35;
  const s = floor + (1 - floor) * Math.min(Math.max(intensity, 0), 1);
  if (side === 'bid') {
    return `rgb(${Math.round(16 * (1 - s))}, ${Math.round(60 + s * 130)}, ${Math.round(40 + s * 90)})`;
  }
  return `rgb(${Math.round(80 + s * 160)}, ${Math.round(30 * (1 - s) + 20)}, ${Math.round(30 * (1 - s) + 20)})`;
};

const WallEvent = ({ event }) => {
  const isBid = event.side === 'bid';
  const appeared = event.event === 'appeared';
  return (
    <div className={`flex items-center gap-1.5 text-[10px] px-2 py-1 rounded-md border ${
      appeared
        ? (isBid ? 'bg-green-500/10 border-emerald-500/20 text-lime-400' : 'bg-red-500/10 border-red-500/20 text-orange-400')
        : 'bg-slate-700/60 border-slate-400/30 text-slate-400'
    }`} data-testid={`wall-event-${event.event}`}>
      {appeared ? <Zap className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
      <span className="font-mono">${event.price?.toLocaleString()}</span>
      <span>{appeared ? 'appeared' : 'vanished'}</span>
      {event.ratio && <span className="opacity-60">({event.ratio}x)</span>}
    </div>
  );
};

const OrderFlowHeatmap = ({ symbol = 'BTC' }) => {
  const [connected, setConnected] = useState(false);
  const [snapshots, setSnapshots] = useState([]);
  const [latest, setLatest] = useState(null);
  const [wallEvents, setWallEvents] = useState([]);
  const heatmapRef = useRef(null);

  const getSseUrl = useCallback(() => {
    const base = getApiBase();
    return `${base}/api/stream/orderflow/${symbol}`;
  }, [symbol]);

  useEffect(() => {
    let es;
    let reconnectTimer;

    const connect = () => {
      const url = getSseUrl();
      es = new EventSource(url);

      es.onopen = () => {
        setConnected(true);
      };

      es.addEventListener('history', (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.snapshots) {
            setSnapshots(data.snapshots.slice(-MAX_COLS));
            const last = data.snapshots[data.snapshots.length - 1];
            if (last) setLatest(last);
          }
        } catch {}
      });

      es.addEventListener('snapshot', (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === 'snapshot') {
            setSnapshots(prev => {
              const next = [...prev, data];
              return next.length > MAX_COLS ? next.slice(-MAX_COLS) : next;
            });
            setLatest(data);

            if (data.wall_events?.length > 0) {
              setWallEvents(prev => [...data.wall_events, ...prev].slice(0, 8));
            }
          }
        } catch {}
      });

      es.onerror = () => {
        setConnected(false);
        es.close();
        reconnectTimer = setTimeout(connect, 3000);
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (es) {
        es.close();
      }
    };
  }, [getSseUrl]);

  // Build price bins for consistent Y axis
  const { bins, grid } = React.useMemo(() => {
    if (!snapshots.length) return { bins: [], grid: [] };

    // Find price range across all snapshots
    let minPrice = Infinity, maxPrice = -Infinity;
    snapshots.forEach(snap => {
      snap.bids?.forEach(b => { minPrice = Math.min(minPrice, b.price); maxPrice = Math.max(maxPrice, b.price); });
      snap.asks?.forEach(a => { minPrice = Math.min(minPrice, a.price); maxPrice = Math.max(maxPrice, a.price); });
    });

    if (minPrice >= maxPrice) return { bins: [], grid: [] };

    const range = maxPrice - minPrice;
    const numBins = 24;
    const binSize = range / numBins;

    // Create bins (high to low)
    const binList = [];
    for (let i = numBins - 1; i >= 0; i--) {
      binList.push({
        price: minPrice + (i + 0.5) * binSize,
        low: minPrice + i * binSize,
        high: minPrice + (i + 1) * binSize,
      });
    }

    // Build grid: for each bin, find the max intensity bid/ask across levels in that bin
    const gridData = binList.map(bin => ({
      price: bin.price,
      cells: snapshots.map(snap => {
        let bestBid = { intensity: 0, qty: 0 };
        let bestAsk = { intensity: 0, qty: 0 };

        snap.bids?.forEach(b => {
          if (b.price >= bin.low && b.price < bin.high && b.intensity > bestBid.intensity) {
            bestBid = { intensity: b.intensity, qty: b.qty };
          }
        });
        snap.asks?.forEach(a => {
          if (a.price >= bin.low && a.price < bin.high && a.intensity > bestAsk.intensity) {
            bestAsk = { intensity: a.intensity, qty: a.qty };
          }
        });

        if (bestBid.intensity > 0 && bestAsk.intensity > 0) {
          return bestBid.intensity >= bestAsk.intensity
            ? { intensity: bestBid.intensity, side: 'bid', qty: bestBid.qty }
            : { intensity: bestAsk.intensity, side: 'ask', qty: bestAsk.qty };
        }
        if (bestBid.intensity > 0) return { intensity: bestBid.intensity, side: 'bid', qty: bestBid.qty };
        if (bestAsk.intensity > 0) return { intensity: bestAsk.intensity, side: 'ask', qty: bestAsk.qty };
        return { intensity: 0, side: 'none', qty: 0 };
      }),
    }));

    return { bins: binList, grid: gridData };
  }, [snapshots]);

  const biasColor = latest?.bias === 'INSTITUTIONAL_BID' ? 'text-lime-400' : latest?.bias === 'INSTITUTIONAL_ASK' ? 'text-orange-400' : 'text-slate-400';

  return (
    <div className="space-y-3" data-testid="orderflow-heatmap">
      {/* Status bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`flex items-center gap-1.5 text-[10px] px-2 py-1 rounded-full border ${
            connected ? 'bg-green-500/10 border-emerald-500/20 text-lime-400' : 'bg-red-500/10 border-red-500/20 text-orange-400'
          }`} data-testid="ws-status">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
            {connected ? 'LIVE' : 'CONNECTING'}
          </div>
          {latest && (
            <>
              <span className="text-white text-sm font-mono font-bold">${latest.mid?.toLocaleString()}</span>
              <span className="text-slate-400 text-[10px]">
                Spread: ${latest.spread} ({latest.spread_pct}%)
              </span>
            </>
          )}
        </div>
        {latest && (
          <div className="flex items-center gap-3 text-[10px]">
            <span className={biasColor}>{latest.bias?.replace('_', ' ')}</span>
            <span className="text-slate-400">
              Bids: <span className="text-lime-400 font-mono">${(latest.bid_total / 1000).toFixed(0)}K</span>
              {' / '}
              Asks: <span className="text-orange-400 font-mono">${(latest.ask_total / 1000).toFixed(0)}K</span>
            </span>
          </div>
        )}
      </div>

      {/* Bid/Ask pressure bar */}
      {latest && (
        <div className="h-2 rounded-full overflow-hidden flex bg-slate-900" data-testid="pressure-bar">
          <div className="bg-green-500/60 transition-all duration-700" style={{ width: `${latest.bid_pct}%` }} />
          <div className="bg-red-500/60 transition-all duration-700" style={{ width: `${100 - latest.bid_pct}%` }} />
        </div>
      )}

      {/* Heatmap Grid */}
      {grid.length > 0 ? (
        <div className="relative overflow-hidden rounded-lg border border-slate-400/30/60 bg-slate-700/60" ref={heatmapRef}>
          <div className="overflow-x-hidden">
            <div className="min-w-full">
              {grid.map((row, ri) => {
                const binSize = grid.length > 1 ? Math.abs(grid[0].price - grid[1].price) : 999;
                const isMidRow = latest && Math.abs(row.price - latest.mid) < binSize;
                return (
                <div key={ri} className={`flex items-center ${isMidRow ? 'border-y border-yellow-500/40' : ''}`} style={{ height: '16px' }}>
                  {/* Price label */}
                  <div className="w-20 shrink-0 text-right pr-2 text-[9px] font-mono text-slate-400">
                    ${row.price >= 1000 ? row.price.toFixed(0).toLocaleString() : row.price.toFixed(2)}
                  </div>
                  {/* Cells */}
                  <div className="flex-1 flex h-full">
                    {row.cells.map((cell, ci) => (
                      <div
                        key={ci}
                        className="flex-1 h-full transition-colors duration-500"
                        style={{
                          backgroundColor: cell.side !== 'none'
                            ? intensityToColor(cell.intensity, cell.side)
                            : '#283548',
                          minWidth: '6px',
                        }}
                        title={cell.qty > 0 ? `$${row.price.toFixed(0)} | ${cell.qty.toFixed(4)} | ${cell.side}` : ''}
                      />
                    ))}
                  </div>
                </div>
                );
              })}
            </div>
          </div>
          {/* Mid price indicator */}
          {latest && (
            <div className="absolute right-2 top-1 text-[9px] text-slate-400 flex items-center gap-1">
              <ArrowUpDown className="w-3 h-3" />
              {grid.length} bins · {snapshots.length}s
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-10 text-slate-400">
          <Activity className="w-6 h-6 mb-2 animate-pulse" />
          <p className="text-xs">{connected ? 'Building heatmap...' : 'Connecting to Binance stream...'}</p>
        </div>
      )}

      {/* Live walls */}
      {latest?.walls?.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] text-slate-400 uppercase tracking-wider">Active Walls</p>
          <div className="grid grid-cols-2 gap-1">
            {latest.walls.slice(0, 6).map((w, i) => (
              <div key={i} className={`flex items-center justify-between text-[10px] px-2 py-1 rounded border ${
                w.side === 'bid' ? 'border-emerald-500/20 bg-green-500/5' : 'border-red-500/20 bg-red-500/5'
              }`}>
                <span className={`font-mono ${w.side === 'bid' ? 'text-lime-400' : 'text-orange-400'}`}>
                  ${w.price.toLocaleString()}
                </span>
                <span className="text-slate-400">
                  {w.intensity != null ? `${w.intensity}/100` : `${w.ratio}x`} · {w.strength}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Wall events feed */}
      {wallEvents.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] text-slate-400 uppercase tracking-wider">Wall Movements</p>
          <div className="flex flex-wrap gap-1">
            {wallEvents.slice(0, 6).map((ev, i) => (
              <WallEvent key={i} event={ev} />
            ))}
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center justify-center gap-4 text-[9px] text-slate-400">
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded-sm" style={{ background: 'rgb(20, 160, 110)' }} />
          <span>Bid Volume</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded-sm" style={{ background: 'rgb(220, 60, 60)' }} />
          <span>Ask Volume</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded-sm border border-yellow-500/40 bg-yellow-500/10" />
          <span>Wall (&gt;3x median)</span>
        </div>
      </div>
    </div>
  );
};

export default OrderFlowHeatmap;
