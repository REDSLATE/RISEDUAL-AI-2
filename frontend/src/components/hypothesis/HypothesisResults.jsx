import React from 'react';
import { TrendingUp, TrendingDown, Minus, BarChart3, Globe, Landmark, Download, Network, Brain } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import ConsensusReceipt from './ConsensusReceipt';

const verdictColor = (v) => {
  if (v === 'BUY') return 'text-lime-400 bg-lime-700 border-emerald-700/50';
  if (v === 'SELL') return 'text-orange-400 bg-orange-800 border-red-700/50';
  return 'text-amber-300 bg-amber-900/30 border-amber-700/50';
};

const miniVerdictColor = (v) => {
  if (v === 'BUY') return 'text-lime-400';
  if (v === 'SELL') return 'text-orange-400';
  if (v === 'ERROR') return 'text-slate-400';
  return 'text-amber-300';
};

const VerdictIcon = ({ verdict }) => {
  if (verdict === 'BUY') return <TrendingUp className="w-6 h-6 text-lime-400" />;
  if (verdict === 'SELL') return <TrendingDown className="w-6 h-6 text-orange-400" />;
  return <Minus className="w-6 h-6 text-amber-300" />;
};

const HypothesisResults = ({ hypothesis, currentModel, models, onExport, exporting }) => (
  <div className="space-y-5" data-testid="hypothesis-full">
    {/* Verdict Card */}
    <Card className={`border-2 rounded-xl p-6 ${verdictColor(hypothesis.verdict)}`}>
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <VerdictIcon verdict={hypothesis.verdict} />
          <div>
            <div className="text-3xl font-black">{hypothesis.verdict}</div>
            <div className="text-sm opacity-70">{hypothesis.symbol}</div>
          </div>
        </div>
        <div className="text-right flex flex-col items-end gap-1">
          <div className="text-2xl font-bold">{hypothesis.confidence}%</div>
          <div className="text-sm opacity-70">Confidence</div>
          {hypothesis.agreement != null && (
            <div className="text-xs opacity-60">{hypothesis.agreement}% model agreement</div>
          )}
          <div className="flex items-center gap-2 mt-1">
            <Badge className={`text-[9px] border ${currentModel.bg} ${currentModel.color.replace('text-', 'border-').replace('-400', '-700/50')}`} data-testid="model-badge">
              {hypothesis.model || currentModel.label}
            </Badge>
            {hypothesis.multi_agent && (
              <Badge className="text-[9px] bg-violet-800/40 text-violet-300 border-violet-700/50">
                {hypothesis.agents_used || 4} AI Brains
              </Badge>
            )}
            <Button size="sm" variant="outline" className="border-white/20 text-white/80 hover:bg-white/10 rounded-lg text-[10px] h-7 px-2" onClick={onExport} disabled={exporting} data-testid="export-report-btn">
              <Download className="w-3 h-3 mr-1" /> Export
            </Button>
          </div>
        </div>
      </div>
      {hypothesis.summary && (
        <p className="mt-4 text-sm opacity-90">{hypothesis.summary}</p>
      )}
    </Card>

    {/* Decision Receipt — doctrine telemetry for council disagreement
        and execution layering. Only renders when receipt fields are
        present (consensus path emits them; older cached results
        without them stay clean). */}
    <ConsensusReceipt hypothesis={hypothesis} />

    {/* Consensus Mode: Individual Model Results */}
    {hypothesis.individual_results?.length > 0 && (
      <ConsensusBreakdown results={hypothesis.individual_results} models={models} />
    )}

    {/* Price Targets */}
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-5">
        <h3 className="text-slate-300 text-xs font-medium uppercase mb-2">Short-Term Target (1-2 weeks)</h3>
        <p className="text-white text-lg font-bold">{hypothesis.price_target_short || 'N/A'}</p>
      </Card>
      <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-5">
        <h3 className="text-slate-300 text-xs font-medium uppercase mb-2">Medium-Term Target (1-3 months)</h3>
        <p className="text-white text-lg font-bold">{hypothesis.price_target_medium || 'N/A'}</p>
      </Card>
    </div>

    {/* Thesis */}
    {hypothesis.thesis && (
      <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-5">
        <h3 className="text-white font-semibold mb-3">Investment Thesis</h3>
        <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-line">{hypothesis.thesis}</p>
      </Card>
    )}

    {/* Catalysts & Risks */}
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {hypothesis.catalysts?.length > 0 && (
        <Card className="bg-green-600 border-lime-700/30 rounded-xl p-5">
          <h3 className="text-lime-400 font-semibold mb-3 flex items-center gap-2">
            <TrendingUp className="w-4 h-4" /> Strategist Catalysts
          </h3>
          <ul className="space-y-2">
            {hypothesis.catalysts.map((c, i) => (
              <li key={`catalyst-${i}-${c.slice(0,20)}`} className="text-slate-300 text-sm flex items-start gap-2">
                <span className="text-emerald-500 mt-1">+</span> {c}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {hypothesis.risks?.length > 0 && (
        <Card className="bg-red-500/20 border-orange-700/30 rounded-xl p-5">
          <h3 className="text-orange-400 font-semibold mb-3 flex items-center gap-2">
            <TrendingDown className="w-4 h-4" /> Auditor Risk Flags
          </h3>
          <ul className="space-y-2">
            {hypothesis.risks.map((r, i) => (
              <li key={`risk-${i}-${r.slice(0,20)}`} className="text-slate-300 text-sm flex items-start gap-2">
                <span className="text-red-500 mt-1">-</span> {r}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>

    {/* Congressional Activity & Sector Impact */}
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {hypothesis.congressional_activity && (
        <Card className="bg-violet-950/20 border-violet-800/30 rounded-xl p-5">
          <h3 className="text-violet-300 font-semibold mb-3 flex items-center gap-2">
            <Landmark className="w-4 h-4" /> Congressional Activity
          </h3>
          <p className="text-slate-300 text-sm">{hypothesis.congressional_activity}</p>
        </Card>
      )}
      {hypothesis.sector_impact && (
        <Card className="bg-blue-950/20 border-blue-800/30 rounded-xl p-5">
          <h3 className="text-blue-400 font-semibold mb-3 flex items-center gap-2">
            <Globe className="w-4 h-4" /> Sector & Macro Impact
          </h3>
          <p className="text-slate-300 text-sm">{hypothesis.sector_impact}</p>
        </Card>
      )}
    </div>

    {/* Technical Outlook */}
    {hypothesis.technical_outlook && (
      <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-5">
        <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-[#3DE8D9]" /> Technical Outlook
        </h3>
        <p className="text-slate-300 text-sm">{hypothesis.technical_outlook}</p>
      </Card>
    )}

    {/* Strategist & Auditor Analyses */}
    {hypothesis.agent_analyses?.length > 0 && (
      <Card className="bg-slate-700/60 border-violet-800/30 rounded-xl p-5" data-testid="agent-analyses">
        <details>
          <summary className="flex items-center gap-2 cursor-pointer text-violet-300 font-semibold text-sm hover:text-violet-300 transition-colors">
            <Brain className="w-4 h-4" />
            Strategist & Auditor Breakdown ({hypothesis.agent_analyses.length} Agents)
          </summary>
          <div className="mt-3 space-y-3">
            {hypothesis.agent_analyses.map((a) => (
              <div key={`${a.role}-${a.summary?.slice(0,20)}`} className="bg-slate-800/50 rounded-lg p-3">
                <span className="text-violet-300 text-xs font-semibold">{a.role}</span>
                <p className="text-slate-300 text-xs leading-relaxed mt-1">{a.summary}</p>
              </div>
            ))}
          </div>
        </details>
      </Card>
    )}
  </div>
);

const ConsensusBreakdown = ({ results, models }) => (
  <Card className="bg-slate-700/60 border-violet-800/30 rounded-xl p-5" data-testid="consensus-breakdown">
    <h3 className="text-violet-300 font-semibold mb-4 flex items-center gap-2">
      <Network className="w-4 h-4" /> Individual Model Verdicts
    </h3>
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
      {results.map((r) => {
        const modelDef = models.find(m => m.key === r.model_key) || models[0];
        const ModelIcon = modelDef.icon;
        return (
          <div key={r.model_key} className="bg-slate-800/50 border border-slate-400/30/40 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <ModelIcon className={`w-4 h-4 ${modelDef.color}`} />
              <span className="text-white text-xs font-medium">{r.model}</span>
            </div>
            <div className={`text-xl font-black ${miniVerdictColor(r.verdict)}`}>
              {r.error ? 'FAILED' : r.verdict}
            </div>
            <div className="text-slate-300 text-xs mt-1">
              {r.error ? 'Model error' : `${r.confidence}% confidence`}
            </div>
          </div>
        );
      })}
    </div>
  </Card>
);

export default HypothesisResults;
