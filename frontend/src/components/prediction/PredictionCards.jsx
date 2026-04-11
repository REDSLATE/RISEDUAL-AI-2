import React from 'react';
import { TrendingUp, TrendingDown, Activity, Globe, Landmark, Brain } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';

const verdictColor = (v) => {
  if (!v) return 'text-amber-300';
  const upper = v.toUpperCase();
  if (upper.includes('BULLISH') || upper === 'UP') return 'text-lime-400';
  if (upper.includes('BEARISH') || upper === 'DOWN') return 'text-orange-400';
  return 'text-amber-300';
};

const PredictionCard = ({ prediction }) => {
  if (!prediction) return null;
  const verdict = prediction.overall_direction || prediction.verdict;
  const confidence = prediction.confidence_score ?? prediction.confidence;

  return (
    <Card className="bg-slate-700/45 border-slate-400/30/40 rounded-xl p-6" data-testid="prediction-main-card">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <h3 className="text-white text-lg font-semibold" style={{ fontFamily: 'Manrope, sans-serif' }}>Market Outlook</h3>
          {prediction.multi_agent && (
            <Badge className="bg-violet-800/40 text-violet-300 text-[9px]">
              {prediction.agents_used || 4} AI Agents
            </Badge>
          )}
        </div>
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
            <span className="text-slate-300 text-xs">AI Confidence</span>
            <span className="text-white text-xs font-bold">{confidence}%</span>
          </div>
          <div className="w-full bg-slate-700/50 rounded-full h-2">
            <div
              className="h-2 rounded-full transition-all bg-gradient-to-r from-[#3DE8D9] to-cyan-400"
              style={{ width: `${confidence}%` }}
            />
          </div>
        </div>
      )}

      {/* Multi-agent crew insights */}
      {prediction.agent_consensus && (
        <div className="mt-3 bg-violet-900/15 border border-violet-800/25 rounded-lg p-3">
          <div className="flex items-center gap-1.5 mb-1">
            <Brain className="w-3 h-3 text-violet-300" />
            <span className="text-violet-300 text-[10px] font-semibold uppercase">Agent Consensus</span>
          </div>
          <p className="text-slate-300 text-xs">{prediction.agent_consensus}</p>
        </div>
      )}

      {(prediction.institutional_flow || prediction.geopolitical_impact) && (
        <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
          {prediction.institutional_flow && (
            <div className="bg-slate-800/50 rounded-lg p-2.5">
              <span className="text-blue-400 text-[10px] font-semibold">Institutional Flow</span>
              <p className="text-slate-300 text-xs mt-0.5">{prediction.institutional_flow}</p>
            </div>
          )}
          {prediction.geopolitical_impact && (
            <div className="bg-slate-800/50 rounded-lg p-2.5">
              <span className="text-amber-300 text-[10px] font-semibold">Geopolitical Impact</span>
              <p className="text-slate-300 text-xs mt-0.5">{prediction.geopolitical_impact}</p>
            </div>
          )}
        </div>
      )}

      {prediction.agent_analyses?.length > 0 && (
        <details className="mt-3">
          <summary className="text-slate-400 text-[10px] cursor-pointer hover:text-slate-300 transition-colors">
            View agent analyses ({prediction.agent_analyses.length} agents)
          </summary>
          <div className="mt-2 space-y-2">
            {prediction.agent_analyses.map((a, i) => (
              <div key={i} className="bg-slate-800/50 rounded-lg p-2.5">
                <span className="text-violet-300 text-[10px] font-semibold">{a.role}</span>
                <p className="text-slate-400 text-[11px] mt-0.5">{a.summary}</p>
              </div>
            ))}
          </div>
        </details>
      )}
    </Card>
  );
};

const TimeframeCard = ({ label, tf }) => {
  const dir = (tf.direction || '').toUpperCase();
  const isUp = dir.includes('BULLISH') || dir === 'UP';
  const isDown = dir.includes('BEARISH') || dir === 'DOWN';
  const Icon = isUp ? TrendingUp : isDown ? TrendingDown : Activity;
  const iconColor = isUp ? 'text-lime-400' : isDown ? 'text-orange-400' : 'text-amber-300';

  return (
    <Card className="bg-slate-900/60 border-slate-400/30/30 rounded-lg p-3" data-testid={`timeframe-${label}`}>
      <div className="flex items-center gap-2 mb-2">
        <Icon className={`w-4 h-4 ${iconColor}`} />
        <span className="text-white text-xs font-semibold capitalize">{label.replace('_', ' ')}</span>
      </div>
      <p className="text-slate-300 text-[11px] font-medium mb-1">{tf.direction}</p>
      <p className="text-slate-300 text-xs">{tf.target || tf.summary || tf.outlook || 'N/A'}</p>
    </Card>
  );
};

const MacroDataSection = ({ macroData }) => {
  if (!macroData) return null;

  const we = macroData.world_events || {};
  const fm = macroData.foreign_markets || {};
  const gf = macroData.gov_filings || {};

  const cards = [
    { icon: Globe, color: 'text-lime-400', bgColor: 'bg-lime-800 border-lime-700/30', title: 'World Events', values: [
      { label: 'Total Events', value: we.total || 0 },
      { label: 'High Impact', value: we.high_impact || 0 },
    ], extra: we.top_sectors?.length > 0 ? `Sectors: ${we.top_sectors.join(', ')}` : null },
    { icon: Activity, color: 'text-blue-400', bgColor: 'bg-blue-900/20 border-blue-800/30', title: 'Foreign Markets', values: [
      { label: 'Indices Tracked', value: fm.total_indices || 0 },
      { label: 'Correlation Signals', value: fm.correlation_signals?.length || 0 },
    ], extra: null },
    { icon: Landmark, color: 'text-violet-300', bgColor: 'bg-violet-900/20 border-violet-800/30', title: 'Government Filings', values: [
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
              <span className="text-slate-300 text-xs">{v.label}</span>
              <span className="text-white text-sm font-bold">{v.value}</span>
            </div>
          ))}
          {extra && <p className="text-slate-400 text-[10px] mt-2">{extra}</p>}
        </Card>
      ))}
    </div>
  );
};

const RealEstateSection = ({ realEstate }) => {
  if (!realEstate) return null;

  const healthColor = (h) => {
    if (h === 'healthy' || h === 'growing') return 'text-lime-400';
    if (h === 'declining' || h === 'weak') return 'text-orange-400';
    return 'text-amber-300';
  };

  return (
    <Card className="bg-slate-700/45 border-slate-400/30/40 rounded-xl p-5" data-testid="real-estate-section">
      <h3 className="text-white text-sm font-semibold mb-3">Real Estate Outlook</h3>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <span className="text-slate-300 text-xs block">Housing Market</span>
          <span className={`text-sm font-bold capitalize ${healthColor(realEstate.housing_health)}`}>
            {realEstate.housing_health || 'N/A'}
          </span>
        </div>
        <div>
          <span className="text-slate-300 text-xs block">Commercial</span>
          <span className="text-amber-300 text-sm font-bold capitalize">
            {realEstate.commercial_trend || 'N/A'}
          </span>
        </div>
      </div>
    </Card>
  );
};

export { PredictionCard, MacroDataSection, RealEstateSection };
