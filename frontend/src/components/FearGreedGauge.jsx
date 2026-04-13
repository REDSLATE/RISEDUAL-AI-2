import React, { useState, useEffect } from 'react';
import { Card } from './ui/card';
import { Activity, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import axios from 'axios';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';
import InfoTooltip from './InfoTooltip';

const API = `${getApiBase()}/api`;

const GAUGE_COLORS = [
  { max: 25, color: '#EF4444', bg: 'bg-red-500/20', label: 'Extreme Fear' },
  { max: 45, color: '#F97316', bg: 'bg-orange-500/20', label: 'Fear' },
  { max: 55, color: '#EAB308', bg: 'bg-yellow-500/20', label: 'Neutral' },
  { max: 75, color: '#84CC16', bg: 'bg-lime-500/20', label: 'Greed' },
  { max: 100, color: '#22C55E', bg: 'bg-green-500/20', label: 'Extreme Greed' },
];

const getGaugeStyle = (value) => GAUGE_COLORS.find(g => value < g.max) || GAUGE_COLORS[4];

const GaugeArc = ({ value }) => {
  const style = getGaugeStyle(value);
  const angle = (value / 100) * 180 - 90;
  const r = 70;
  const cx = 90, cy = 85;

  const arcSegments = GAUGE_COLORS.map((seg, i) => {
    const startAngle = ((i === 0 ? 0 : GAUGE_COLORS[i-1].max) / 100) * 180 - 90;
    const endAngle = (seg.max / 100) * 180 - 90;
    const x1 = cx + r * Math.cos((startAngle * Math.PI) / 180);
    const y1 = cy + r * Math.sin((startAngle * Math.PI) / 180);
    const x2 = cx + r * Math.cos((endAngle * Math.PI) / 180);
    const y2 = cy + r * Math.sin((endAngle * Math.PI) / 180);
    return (
      <path
        key={seg.label}
        d={`M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`}
        fill="none"
        stroke={seg.color}
        strokeWidth="10"
        opacity="0.3"
      />
    );
  });

  const nx = cx + (r - 15) * Math.cos((angle * Math.PI) / 180);
  const ny = cy + (r - 15) * Math.sin((angle * Math.PI) / 180);

  return (
    <svg viewBox="0 0 180 100" className="w-full max-w-[200px] mx-auto">
      {arcSegments}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={style.color} strokeWidth="2.5" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="4" fill={style.color} />
      <text x={cx} y={cy + 2} textAnchor="middle" fill="white" fontSize="18" fontWeight="bold">
        {Math.round(value)}
      </text>
    </svg>
  );
};

const Sparkline = ({ data }) => {
  if (!data || data.length < 2) return null;
  const values = data.map(d => d.index);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 200, h = 40;
  const points = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / range) * h}`).join(' ');
  const lastVal = values[values.length - 1];
  const style = getGaugeStyle(lastVal);

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-10">
      <polyline points={points} fill="none" stroke={style.color} strokeWidth="1.5" opacity="0.7" />
    </svg>
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
        <div className="h-32 bg-slate-600/30 rounded-lg" />
      </Card>
    );
  }

  const current = data.current || {};
  const style = getGaugeStyle(current.value || 50);
  const Icon = current.value > 55 ? TrendingUp : current.value < 45 ? TrendingDown : Minus;

  return (
    <Card className="bg-slate-700/60 border-slate-400/30 rounded-xl p-4" data-testid="fear-greed-gauge">
      <div className="flex items-center gap-2 mb-3">
        <Activity className="w-4 h-4 text-[#3DE8D9]" />
        <h3 className="text-white text-sm font-semibold">Fear & Greed Index</h3>
        <InfoTooltip id="fear-greed" />
        {current.source === 'cnn_live' && (
          <span className="ml-auto text-[9px] bg-green-900/40 text-green-400 px-1.5 py-0.5 rounded-full">LIVE</span>
        )}
      </div>

      <GaugeArc value={current.value || 50} />

      <div className="text-center mt-1">
        <div className="flex items-center justify-center gap-1.5">
          <Icon className="w-4 h-4" style={{ color: style.color }} />
          <span className="text-sm font-bold" style={{ color: style.color }}>{current.label}</span>
        </div>
        <p className="text-slate-400 text-[10px] mt-0.5">{current.date}</p>
      </div>

      <div className="grid grid-cols-2 gap-2 mt-3">
        <div className="bg-slate-800/50 rounded-lg px-2.5 py-1.5 text-center">
          <p className="text-[10px] text-slate-400">7d Avg</p>
          <p className="text-sm font-bold" style={{ color: getGaugeStyle(data.avg_7d).color }}>{data.avg_7d}</p>
        </div>
        <div className="bg-slate-800/50 rounded-lg px-2.5 py-1.5 text-center">
          <p className="text-[10px] text-slate-400">30d Avg</p>
          <p className="text-sm font-bold" style={{ color: getGaugeStyle(data.avg_30d).color }}>{data.avg_30d}</p>
        </div>
      </div>

      {data.history && data.history.length > 5 && (
        <div className="mt-3">
          <p className="text-[10px] text-slate-400 mb-1">90-Day Trend</p>
          <Sparkline data={data.history} />
        </div>
      )}
    </Card>
  );
};

export default FearGreedGauge;
