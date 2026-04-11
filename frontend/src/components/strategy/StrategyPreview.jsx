import React from 'react';
import { Save, TrendingUp, TrendingDown, Shield, Clock, Target, Sparkles, AlertTriangle } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';

const CollapsibleSection = ({ title, icon, section, expanded, toggle, children }) => (
  <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl overflow-hidden">
    <button
      onClick={() => toggle(section)}
      className="w-full flex items-center justify-between px-5 py-3 hover:bg-slate-800/80 transition-colors"
      data-testid={`section-toggle-${section}`}
    >
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-white text-sm font-semibold">{title}</span>
      </div>
      {expanded === section ? <span className="text-slate-400">-</span> : <span className="text-slate-400">+</span>}
    </button>
    {expanded === section && <div className="px-5 pb-4">{children}</div>}
  </Card>
);

const RuleCard = ({ rule, index, color }) => (
  <div className={`bg-slate-900/60 rounded-lg p-3 border border-${color}-800/20`}>
    <div className="flex items-center gap-2 mb-1">
      <span className={`w-5 h-5 rounded-full bg-${color}-900/40 text-${color}-400 text-[10px] flex items-center justify-center font-bold`}>{index + 1}</span>
      <span className="text-white text-sm font-medium">{rule.condition}</span>
      {rule.priority && <Badge className="bg-slate-700/60 text-slate-400 text-[9px]">P{rule.priority}</Badge>}
    </div>
    <p className="text-slate-400 text-xs ml-7">{rule.description}</p>
  </div>
);

const StrategyPreview = ({ strategy, onSave, saving, expanded, toggle }) => (
  <div className="space-y-4" data-testid="strategy-results">
    {/* Header Card */}
    <Card className="bg-gradient-to-r from-violet-950/40 to-fuchsia-950/40 border-violet-800/40 rounded-xl p-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 className="text-white text-lg font-bold">{strategy.name}</h3>
          <p className="text-slate-300 text-sm mt-1">{strategy.summary}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge className="bg-violet-900/50 text-violet-300 border-violet-700/50 text-[10px]">{strategy.timeframe}</Badge>
          {strategy.asset_classes?.map(ac => (
            <Badge key={ac} className="bg-slate-700/60 text-slate-300 border-slate-600 text-[10px] capitalize">{ac}</Badge>
          ))}
        </div>
      </div>
      <div className="flex gap-2 mt-4">
        <Button size="sm" onClick={onSave} disabled={saving}
          className="bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-xs h-8" data-testid="save-strategy-btn">
          <Save className="w-3.5 h-3.5 mr-1" /> {saving ? 'Saving...' : 'Save Strategy'}
        </Button>
        <Badge className="bg-slate-800 text-slate-400 border-slate-700 text-[9px]">
          <Sparkles className="w-3 h-3 mr-1" /> {strategy.model_used || 'GPT-5.2'}
        </Badge>
      </div>
    </Card>

    {/* Indicators */}
    {strategy.indicators?.length > 0 && (
      <CollapsibleSection title="Technical Indicators" icon={<Target className="w-4 h-4 text-blue-400" />} section="indicators" expanded={expanded} toggle={toggle}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {strategy.indicators.map((ind, i) => (
            <div key={`ind-${i}`} className="bg-slate-900/60 rounded-lg p-3 border border-slate-700/30">
              <div className="flex items-center gap-2">
                <span className="text-white text-sm font-semibold">{ind.name}</span>
                {ind.period && <Badge className="bg-blue-900/30 text-blue-400 border-blue-800/40 text-[9px]">Period: {ind.period}</Badge>}
              </div>
              <p className="text-slate-400 text-xs mt-1">{ind.description}</p>
            </div>
          ))}
        </div>
      </CollapsibleSection>
    )}

    {/* Entry Rules */}
    {strategy.entry_rules?.length > 0 && (
      <CollapsibleSection title="Entry Rules" icon={<TrendingUp className="w-4 h-4 text-emerald-400" />} section="entry" expanded={expanded} toggle={toggle}>
        <div className="space-y-2">
          {strategy.entry_rules.map((rule, i) => (
            <RuleCard key={`entry-${i}`} rule={rule} index={i} color="emerald" />
          ))}
        </div>
      </CollapsibleSection>
    )}

    {/* Exit Rules */}
    {strategy.exit_rules?.length > 0 && (
      <CollapsibleSection title="Exit Rules" icon={<TrendingDown className="w-4 h-4 text-red-400" />} section="exit" expanded={expanded} toggle={toggle}>
        <div className="space-y-2">
          {strategy.exit_rules.map((rule, i) => (
            <RuleCard key={`exit-${i}`} rule={rule} index={i} color="red" />
          ))}
        </div>
      </CollapsibleSection>
    )}

    {/* Risk Management */}
    {strategy.risk_management && (
      <CollapsibleSection title="Risk Management" icon={<Shield className="w-4 h-4 text-amber-400" />} section="risk" expanded={expanded} toggle={toggle}>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {Object.entries(strategy.risk_management).map(([key, value]) => (
            <div key={key} className="bg-slate-900/60 rounded-lg p-3 border border-slate-700/30">
              <span className="text-slate-500 text-[10px] uppercase tracking-wider">{key.replace(/_/g, ' ')}</span>
              <p className="text-white text-sm font-semibold mt-0.5">{String(value)}</p>
            </div>
          ))}
        </div>
      </CollapsibleSection>
    )}

    {/* Market Conditions & Notes */}
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {strategy.market_conditions && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <Clock className="w-4 h-4 text-[#35D6C8]" />
            <span className="text-slate-400 text-xs font-medium uppercase">Market Conditions</span>
          </div>
          <p className="text-slate-300 text-sm">{strategy.market_conditions}</p>
        </Card>
      )}
      {strategy.backtesting_notes && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <Target className="w-4 h-4 text-violet-400" />
            <span className="text-slate-400 text-xs font-medium uppercase">Backtesting Notes</span>
          </div>
          <p className="text-slate-300 text-sm">{strategy.backtesting_notes}</p>
        </Card>
      )}
    </div>

    {/* Warnings */}
    {strategy.warnings?.length > 0 && (
      <Card className="bg-amber-950/20 border-amber-800/30 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-2">
          <AlertTriangle className="w-4 h-4 text-amber-400" />
          <span className="text-amber-400 text-xs font-semibold uppercase">Warnings</span>
        </div>
        <ul className="space-y-1">
          {strategy.warnings.map((w, i) => (
            <li key={`warn-${i}`} className="text-amber-200/70 text-xs flex items-start gap-2">
              <span className="text-amber-500 mt-0.5">-</span> {w}
            </li>
          ))}
        </ul>
      </Card>
    )}
  </div>
);

export default StrategyPreview;
