import React from 'react';
import { TrendingUp, TrendingDown, Activity, Globe, Landmark } from 'lucide-react';
import { Card } from '../ui/card';

const verdictColor = (v) => {
  if (!v) return 'text-amber-400';
  const upper = v.toUpperCase();
  if (upper.includes('BULLISH') || upper === 'UP') return 'text-emerald-400';
  if (upper.includes('BEARISH') || upper === 'DOWN') return 'text-red-400';
  return 'text-amber-400';
};

const PredictionCard = ({ prediction }) => {
  if (!prediction) return null;
  const verdict = prediction.overall_direction || prediction.verdict;
  const confidence = prediction.confidence_score ?? prediction.confidence;

  return (
    <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-6" data-testid="prediction-main-card">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-white text-lg font-semibold" style={{ fontFamily: 'Manrope, sans-serif' }}>Market Outlook</h3>
        <span className={`text-sm font-bold ${verdictColor(verdict)}`}>
          {verdict}
        </span>
      </div>
      <p className="text-slate-300 text-sm leading-relaxed mb-4">{prediction.summary}</p>

      {prediction.timeframes && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          {Object.entries(prediction.timeframes).map(([key, tf]) => (
            <TimeframeCard key={key} label={key} tf={tf} />
          ))}
        </div>
      )}

      {confidence != null && (
        <div className="mt-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-slate-400 text-xs">AI Confidence</span>
            <span className="text-white text-xs font-bold">{confidence}%</span>
          </div>
          <div className="w-full bg-slate-700/50 rounded-full h-2">
            <div
              className="h-2 rounded-full transition-all bg-gradient-to-r from-[#0052FF] to-cyan-400"
              style={{ width: `${confidence}%` }}
            />
          </div>
        </div>
      )}
    </Card>
  );
};

const TimeframeCard = ({ label, tf }) => {
  const dir = (tf.direction || '').toUpperCase();
  const isUp = dir.includes('BULLISH') || dir === 'UP';
  const isDown = dir.includes('BEARISH') || dir === 'DOWN';
  const Icon = isUp ? TrendingUp : isDown ? TrendingDown : Activity;
  const iconColor = isUp ? 'text-emerald-400' : isDown ? 'text-red-400' : 'text-amber-400';

  return (
    <Card className="bg-slate-900/60 border-slate-700/30 rounded-lg p-3" data-testid={`timeframe-${label}`}>
      <div className="flex items-center gap-2 mb-2">
        <Icon className={`w-4 h-4 ${iconColor}`} />
        <span className="text-white text-xs font-semibold capitalize">{label.replace('_', ' ')}</span>
      </div>
      <p className="text-slate-300 text-[11px] font-medium mb-1">{tf.direction}</p>
      <p className="text-slate-400 text-xs">{tf.target || tf.summary || tf.outlook || 'N/A'}</p>
    </Card>
  );
};

const MacroDataSection = ({ macroData }) => {
  if (!macroData) return null;

  const we = macroData.world_events || {};
  const fm = macroData.foreign_markets || {};
  const gf = macroData.gov_filings || {};

  const cards = [
    { icon: Globe, color: 'text-emerald-400', bgColor: 'bg-emerald-900/20 border-emerald-800/30', title: 'World Events', values: [
      { label: 'Total Events', value: we.total || 0 },
      { label: 'High Impact', value: we.high_impact || 0 },
    ], extra: we.top_sectors?.length > 0 ? `Sectors: ${we.top_sectors.join(', ')}` : null },
    { icon: Activity, color: 'text-blue-400', bgColor: 'bg-blue-900/20 border-blue-800/30', title: 'Foreign Markets', values: [
      { label: 'Indices Tracked', value: fm.total_indices || 0 },
      { label: 'Correlation Signals', value: fm.correlation_signals?.length || 0 },
    ], extra: null },
    { icon: Landmark, color: 'text-violet-400', bgColor: 'bg-violet-900/20 border-violet-800/30', title: 'Government Filings', values: [
      { label: 'Congressional Trades', value: gf.congressional_trades || 0 },
      { label: 'Fed Announcements', value: gf.fed_announcements || 0 },
      { label: 'Insider Trades', value: gf.insider_trades || 0 },
    ], extra: null },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4" data-testid="macro-data-section">
      {cards.map(({ icon: Icon, color, bgColor, title, values, extra }) => (
        <Card key={title} className={`${bgColor} rounded-xl p-5`}>
          <div className="flex items-center gap-2 mb-3">
            <Icon className={`w-4 h-4 ${color}`} />
            <h4 className={`${color} text-sm font-semibold`}>{title}</h4>
          </div>
          {values.map(v => (
            <div key={v.label} className="flex items-center justify-between mb-1">
              <span className="text-slate-400 text-xs">{v.label}</span>
              <span className="text-white text-sm font-bold">{v.value}</span>
            </div>
          ))}
          {extra && <p className="text-slate-500 text-[10px] mt-2">{extra}</p>}
        </Card>
      ))}
    </div>
  );
};

const RealEstateSection = ({ realEstate }) => {
  if (!realEstate) return null;

  const healthColor = (h) => {
    if (h === 'healthy' || h === 'growing') return 'text-emerald-400';
    if (h === 'declining' || h === 'weak') return 'text-red-400';
    return 'text-amber-400';
  };

  return (
    <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5" data-testid="real-estate-section">
      <h3 className="text-white text-sm font-semibold mb-3">Real Estate Outlook</h3>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <span className="text-slate-400 text-xs block">Housing Market</span>
          <span className={`text-sm font-bold capitalize ${healthColor(realEstate.housing_health)}`}>
            {realEstate.housing_health || 'N/A'}
          </span>
        </div>
        <div>
          <span className="text-slate-400 text-xs block">Commercial</span>
          <span className="text-amber-400 text-sm font-bold capitalize">
            {realEstate.commercial_trend || 'N/A'}
          </span>
        </div>
      </div>
    </Card>
  );
};

export { PredictionCard, MacroDataSection, RealEstateSection };
