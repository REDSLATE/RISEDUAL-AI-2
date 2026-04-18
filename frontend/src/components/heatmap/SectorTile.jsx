import React from 'react';
import { TrendingUp, TrendingDown } from 'lucide-react';
import { Badge } from '../ui/badge';
import { openWarRoomForTicker } from '../../utils/deepLink';

const getHeatColor = (val) => {
  if (val >= 3) return 'bg-green-500 text-white';
  if (val >= 1.5) return 'bg-lime-500 text-white';
  if (val >= 0.5) return 'bg-lime-400 text-gray-900';
  if (val >= 0) return 'bg-yellow-400 text-gray-900';
  if (val >= -0.5) return 'bg-yellow-500 text-gray-900';
  if (val >= -1.5) return 'bg-orange-500 text-white';
  if (val >= -3) return 'bg-red-500 text-white';
  return 'bg-red-600 text-white';
};

const getSentimentColor = (val) => {
  if (val >= 75) return 'bg-green-500 text-white';
  if (val >= 55) return 'bg-green-600 text-white';
  if (val >= 45) return 'bg-gray-500 text-white';
  if (val >= 25) return 'bg-orange-500 text-white';
  return 'bg-red-500 text-white';
};

const getSentimentLabel = (val) => {
  if (val >= 75) return 'Strong Buy';
  if (val >= 62) return 'Bullish';
  if (val >= 55) return 'Lean Bull';
  if (val >= 45) return 'Neutral';
  if (val >= 38) return 'Cautious';
  if (val >= 25) return 'Bearish';
  return 'Strong Sell';
};

const Sparkline = ({ points, width = 48, height = 16 }) => {
  if (!points || points.length < 2) return null;
  const vals = points.map(p => p.heatmap_value);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const step = width / (vals.length - 1);
  const pathD = vals.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * height;
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const trending = vals[vals.length - 1] >= vals[0];
  return (
    <svg width={width} height={height} className="opacity-70">
      <path d={pathD} fill="none" stroke={trending ? '#34d399' : '#f87171'} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
};

const SectorTile = ({ sector, isAI, period, sentimentData, sectorTrends }) => {
  if (isAI) {
    const ai = sentimentData[sector.symbol] || {};
    const heatVal = ai.heatmap_value ?? 50;
    const label = ai.label || getSentimentLabel(heatVal);
    const reasoning = ai.reasoning || '';
    const trends = sectorTrends[sector.symbol];

    return (
      <button
        type="button"
        onClick={() => openWarRoomForTicker({ ticker: sector.symbol, source: 'AI Sector Heatmap' })}
        className={`${getSentimentColor(heatVal)} text-left w-full rounded-xl p-4 transition-all hover:scale-[1.02] hover:brightness-110 cursor-pointer relative overflow-hidden`}
        style={{ minHeight: `${Math.max(80, sector.weight * 4)}px` }}
        data-testid={`sector-tile-${sector.symbol}`}
        title={`${reasoning || label} — click to open ${sector.symbol} in War Room`}
      >
        <div className="absolute top-0 right-0 opacity-10 text-6xl font-black leading-none select-none pointer-events-none" style={{ marginTop: '-8px', marginRight: '-4px' }}>
          {sector.symbol}
        </div>
        <div className="relative z-10">
          <div className="flex items-center justify-between mb-1">
            <span className="font-bold text-sm">{sector.symbol}</span>
            <Badge className="bg-purple-900/40 border-purple-700/40 text-[9px] font-medium">{label}</Badge>
          </div>
          <p className="text-xs opacity-80 mb-2">{sector.name}</p>
          <div className="flex items-end justify-between">
            <span className="text-2xl font-black tabular-nums">{heatVal.toFixed(0)}</span>
            <div className="flex flex-col items-end gap-0.5">
              {trends && trends.length >= 2 && <Sparkline points={trends} />}
              <span className="text-[10px] opacity-60">/ 100</span>
            </div>
          </div>
          {reasoning && <p className="text-[10px] opacity-60 mt-1 line-clamp-2">{reasoning}</p>}
        </div>
      </button>
    );
  }

  const val = sector[period] || 0;
  const isUp = val >= 0;

  return (
    <button
      type="button"
      onClick={() => openWarRoomForTicker({ ticker: sector.symbol, source: 'Sector Heatmap' })}
      className={`${getHeatColor(val)} text-left w-full rounded-xl p-4 transition-all hover:scale-[1.02] hover:brightness-110 cursor-pointer relative overflow-hidden`}
      style={{ minHeight: `${Math.max(80, sector.weight * 4)}px` }}
      data-testid={`sector-tile-${sector.symbol}`}
      title={`Click to open ${sector.symbol} in War Room`}
    >
      <div className="absolute top-0 right-0 opacity-10 text-6xl font-black leading-none select-none pointer-events-none" style={{ marginTop: '-8px', marginRight: '-4px' }}>
        {sector.symbol}
      </div>
      <div className="relative z-10">
        <div className="flex items-center justify-between mb-1">
          <span className="font-bold text-sm">{sector.symbol}</span>
          <Badge className="bg-black/20 border-0 text-[9px] font-medium">{sector.weight}%</Badge>
        </div>
        <p className="text-xs opacity-80 mb-2">{sector.name}</p>
        <div className="flex items-end justify-between">
          <span className="text-2xl font-black tabular-nums">{isUp ? '+' : ''}{val.toFixed(2)}%</span>
          {isUp ? <TrendingUp className="w-4 h-4 opacity-60" /> : <TrendingDown className="w-4 h-4 opacity-60" />}
        </div>
        <p className="text-[10px] opacity-60 mt-1">${sector.price?.toFixed(2)}</p>
      </div>
    </button>
  );
};

export { SectorTile, getHeatColor, getSentimentColor, getSentimentLabel };
