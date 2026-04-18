import React, { useState, useEffect } from 'react';
import { Card } from './ui/card';
import { Activity, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import axios from 'axios';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';
import InfoTooltip from './InfoTooltip';

const API = `${getApiBase()}/api`;

// Band styles — mirror the AI War Room verdict palette (lime = bullish,
// orange = bearish) so this card reads as a consistent "verdict tab".
const BANDS = [
  { max: 25,  label: 'EXTREME FEAR', tone: 'bear', verdict: 'VETO',    text: 'text-red-300',    ring: 'border-red-500/50',    fill: 'bg-red-600',     soft: 'bg-red-500/10'  },
  { max: 45,  label: 'FEAR',         tone: 'bear', verdict: 'BEARISH', text: 'text-orange-300', ring: 'border-orange-500/50', fill: 'bg-orange-600',  soft: 'bg-orange-500/10' },
  { max: 55,  label: 'NEUTRAL',      tone: 'flat', verdict: 'HOLD',    text: 'text-amber-200',  ring: 'border-amber-500/50',  fill: 'bg-amber-500',   soft: 'bg-amber-500/10' },
  { max: 75,  label: 'GREED',        tone: 'bull', verdict: 'BULLISH', text: 'text-lime-300',   ring: 'border-lime-500/50',   fill: 'bg-lime-600',    soft: 'bg-lime-500/10' },
  { max: 101, label: 'EXTREME GREED',tone: 'bull', verdict: 'PASS',    text: 'text-green-300',  ring: 'border-green-500/50',  fill: 'bg-green-600',   soft: 'bg-green-500/10' },
];

const bandFor = (v) => BANDS.find((b) => (v ?? 50) < b.max) || BANDS[BANDS.length - 1];

const Sparkline = ({ data }) => {
  if (!data || data.length < 2) return null;
  const values = data.map((d) => d.index);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 200, h = 32;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / range) * h}`)
    .join(' ');
  const lastVal = values[values.length - 1];
  const lastBand = bandFor(lastVal);
  const strokeColor = lastBand.tone === 'bull' ? '#84CC16' : lastBand.tone === 'bear' ? '#F97316' : '#EAB308';
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-8">
      <polyline points={points} fill="none" stroke={strokeColor} strokeWidth="1.5" opacity="0.8" />
    </svg>
  );
};

// Split track — left 50% = fear gradient, right 50% = greed gradient, with a
// marker showing the current reading's position across the full 0..100 range.
const SplitTrack = ({ value }) => {
  const pct = Math.max(0, Math.min(100, value ?? 50));
  const band = bandFor(pct);
  const markerColor = band.tone === 'bull' ? '#84CC16' : band.tone === 'bear' ? '#F97316' : '#EAB308';
  return (
    <div className="relative w-full">
      <div className="h-2.5 rounded-full overflow-hidden flex border border-slate-700/60">
        <div className="flex-1 bg-gradient-to-r from-red-600 via-orange-500 to-amber-400/60" />
        <div className="flex-1 bg-gradient-to-r from-amber-400/60 via-lime-500 to-green-500" />
      </div>
      {/* Vertical marker */}
      <div
        className="absolute top-1/2 -translate-y-1/2 w-0.5 h-4 rounded-full shadow-[0_0_6px_rgba(255,255,255,0.6)]"
        style={{ left: `calc(${pct}% - 1px)`, backgroundColor: markerColor }}
      />
      <div className="flex justify-between text-[9px] uppercase tracking-wider font-semibold mt-1">
        <span className="text-orange-400">Fear</span>
        <span className="text-slate-500">50</span>
        <span className="text-lime-400">Greed</span>
      </div>
    </div>
  );
};

const FearGreedGauge = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await axios.get(`${API}/fear-greed`);
        setData(res.data);
      } catch (err) {
        logger.error('Fear & Greed fetch error:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  if (loading || !data) {
    return (
      <Card className="bg-slate-700/60 border-slate-400/30 rounded-xl p-4 animate-pulse" data-testid="fear-greed-loading">
        <div className="h-40 bg-slate-600/30 rounded-lg" />
      </Card>
    );
  }

  const current = data.current || {};
  const value = Number.isFinite(current.value) ? current.value : 50;
  const band = bandFor(value);
  const TrendIcon = band.tone === 'bull' ? TrendingUp : band.tone === 'bear' ? TrendingDown : Minus;

  const avg7 = bandFor(data.avg_7d);
  const avg30 = bandFor(data.avg_30d);

  return (
    <Card className="bg-slate-800/60 border-slate-400/25 rounded-xl p-4 space-y-3" data-testid="fear-greed-gauge">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Activity className="w-4 h-4 text-[#3DE8D9]" />
        <h3 className="text-white text-sm font-semibold">Fear &amp; Greed</h3>
        <InfoTooltip id="fear-greed" />
        {current.source === 'cnn_live' && (
          <span className="ml-auto text-[9px] font-bold bg-green-900/40 text-green-400 px-1.5 py-0.5 rounded-full border border-green-700/40">
            LIVE
          </span>
        )}
      </div>

      {/* Verdict tab — matches War Room pill style: solid band colour + glowing text */}
      <div
        className={`rounded-xl border ${band.ring} ${band.soft} px-4 py-3`}
        data-testid="fear-greed-verdict"
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <TrendIcon className={`w-4 h-4 ${band.text}`} />
            <span className={`text-[11px] font-bold tracking-wider uppercase ${band.text}`}>
              {band.label}
            </span>
          </div>
          <span
            className={`text-[10px] font-black tracking-wider uppercase px-2 py-0.5 rounded ${band.fill} text-white`}
            data-testid="fear-greed-signal"
          >
            {band.verdict}
          </span>
        </div>
        <div className="mt-1 flex items-baseline gap-1.5">
          <span className={`text-4xl font-black tabular-nums leading-none ${band.text}`} data-testid="fear-greed-value">
            {Math.round(value)}
          </span>
          <span className="text-slate-500 text-xs font-semibold">/100</span>
          {current.date && (
            <span className="ml-auto text-slate-500 text-[10px]">{current.date}</span>
          )}
        </div>
      </div>

      {/* Split fear/greed track with current marker */}
      <SplitTrack value={value} />

      {/* 7d / 30d rolling averages, each color-matched to its band */}
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-lg bg-slate-900/50 border border-slate-700/40 px-2.5 py-1.5">
          <p className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">7d Avg</p>
          <p className={`text-sm font-bold tabular-nums ${avg7.text}`}>
            {Math.round(data.avg_7d || 0)} <span className="text-[9px] font-medium opacity-70">{avg7.label}</span>
          </p>
        </div>
        <div className="rounded-lg bg-slate-900/50 border border-slate-700/40 px-2.5 py-1.5">
          <p className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">30d Avg</p>
          <p className={`text-sm font-bold tabular-nums ${avg30.text}`}>
            {Math.round(data.avg_30d || 0)} <span className="text-[9px] font-medium opacity-70">{avg30.label}</span>
          </p>
        </div>
      </div>

      {/* 90-day trend */}
      {data.history && data.history.length > 5 && (
        <div>
          <p className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold mb-0.5">90-Day Trend</p>
          <Sparkline data={data.history} />
        </div>
      )}
    </Card>
  );
};

export default FearGreedGauge;
