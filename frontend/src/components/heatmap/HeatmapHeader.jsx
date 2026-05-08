import React from 'react';
import { Grid3x3, RefreshCw, Brain } from 'lucide-react';

const PERIODS = [
  { key: 'change_1d', label: '1D' },
  { key: 'change_1w', label: '1W' },
  { key: 'change_1m', label: '1M' },
  { key: 'change_3m', label: '3M' },
  { key: 'change_ytd', label: 'YTD' },
  { key: 'ai_sentiment', label: 'AI' },
];

const HeatmapHeader = ({ period, setPeriod, isAI, loading, sentimentLoading, onRefresh }) => (
  <div className="flex items-center justify-between mb-5 flex-wrap gap-3">
    <div className="flex items-center gap-3">
      <div className="w-10 h-10 bg-gradient-to-br from-orange-600 to-red-600 rounded-xl flex items-center justify-center">
        <Grid3x3 className="w-6 h-6 text-white" />
      </div>
      <div>
        <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>Sector Rotation</h2>
        <p className="text-slate-300 text-xs">
          {isAI ? 'Multi-agent AI sentiment analysis' : 'S&P 500 sector ETF performance heatmap'}
        </p>
      </div>
    </div>
    <div className="flex items-center gap-2 flex-wrap">
      {PERIODS.map(p => (
        <button
          key={p.key}
          onClick={() => setPeriod(p.key)}
          className={`px-3 py-2 min-h-[36px] min-w-[40px] rounded-lg text-xs font-semibold transition-all flex items-center justify-center gap-1 ${
            period === p.key
              ? p.key === 'ai_sentiment'
                ? 'bg-purple-600 text-white ring-1 ring-purple-400/50'
                : 'bg-[#3DE8D9] text-white'
              : 'bg-slate-800/60 text-slate-400 hover:text-white border border-slate-400/25'
          }`}
          data-testid={`period-${p.key}`}
        >
          {p.key === 'ai_sentiment' && <Brain className="w-3 h-3" />}
          {p.label}
        </button>
      ))}
      <button
        onClick={onRefresh}
        className="p-1.5 rounded-lg bg-slate-800/60 text-slate-400 hover:text-white border border-slate-400/25 transition-all ml-1"
        title={isAI ? 'Regenerate AI sentiment' : 'Force refresh (bypass cache)'}
        data-testid="refresh-sectors"
      >
        <RefreshCw className={`w-3.5 h-3.5 ${(loading || sentimentLoading) ? 'animate-spin' : ''}`} />
      </button>
    </div>
  </div>
);

export { HeatmapHeader, PERIODS };
