import React, { useState } from 'react';
import { Search, TrendingUp, TrendingDown, Minus, Lock, Shield, BarChart3, Globe, Landmark, Zap, Sparkles, Download, ChevronDown, Brain, Cpu, Network } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AI_MODELS = [
  { key: 'gpt-5.2', label: 'GPT-5.2', provider: 'OpenAI', icon: Sparkles, color: 'text-emerald-400', bg: 'bg-emerald-900/30', free: true },
  { key: 'claude-sonnet-4.5', label: 'Claude Sonnet 4.5', provider: 'Anthropic', icon: Brain, color: 'text-orange-400', bg: 'bg-orange-900/30', free: false },
  { key: 'gemini-pro', label: 'Gemini Pro', provider: 'Google', icon: Cpu, color: 'text-blue-400', bg: 'bg-blue-900/30', free: false },
  { key: 'consensus', label: 'Consensus Mode', provider: 'All 3 Models', icon: Network, color: 'text-violet-400', bg: 'bg-violet-900/30', free: false },
];

const AIHypothesis = ({ onSubscribe, onLogin }) => {
  const { user, isPro } = useAuth();
  const [symbol, setSymbol] = useState('');
  const [hypothesis, setHypothesis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const [selectedModel, setSelectedModel] = useState('gpt-5.2');
  const [showModelPicker, setShowModelPicker] = useState(false);

  const currentModel = AI_MODELS.find(m => m.key === selectedModel) || AI_MODELS[0];

  const exportReport = () => {
    if (!hypothesis || !hypothesis.is_pro) return;
    setExporting(true);
    try {
      const lines = [
        `RISEDUAL AI - HYPOTHESIS REPORT`,
        `${'='.repeat(50)}`,
        `Symbol: ${hypothesis.symbol}`,
        `Model: ${hypothesis.model || 'GPT-5.2'}`,
        `Generated: ${new Date().toLocaleString()}`,
        ``,
        `VERDICT: ${hypothesis.verdict}`,
        `Confidence: ${hypothesis.confidence}%`,
        hypothesis.agreement != null ? `Model Agreement: ${hypothesis.agreement}%` : '',
        ``,
        `SUMMARY`,
        `${'─'.repeat(10)}`,
        hypothesis.summary || 'N/A',
        ``,
      ];
      if (hypothesis.individual_results?.length) {
        lines.push('INDIVIDUAL MODEL RESULTS', '─'.repeat(25));
        hypothesis.individual_results.forEach(r => {
          lines.push(`${r.model}: ${r.verdict} (${r.confidence}% confidence)`);
        });
        lines.push('');
      }
      if (hypothesis.catalysts?.length) {
        lines.push('CATALYSTS', '─'.repeat(10));
        hypothesis.catalysts.forEach((c, i) => lines.push(`${i + 1}. ${c}`));
        lines.push('');
      }
      if (hypothesis.risks?.length) {
        lines.push('RISKS', '─'.repeat(6));
        hypothesis.risks.forEach((r, i) => lines.push(`${i + 1}. ${r}`));
        lines.push('');
      }
      lines.push('', '(c) RISEDUAL AI - risedual.ai');
      const blob = new Blob([lines.filter(Boolean).join('\n')], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `RISEDUAL_AI_${hypothesis.symbol}_${hypothesis.model || 'Report'}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  const search = async (e) => {
    e?.preventDefault();
    if (!symbol.trim()) return;
    setLoading(true);
    setError('');
    setHypothesis(null);
    try {
      const modelParam = isPro ? selectedModel : 'gpt-5.2';
      const res = await authFetch(`${API}/hypothesis/${symbol.trim().toUpperCase()}?model=${modelParam}`);
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to generate hypothesis');
      }
      const data = await res.json();
      setHypothesis(data);
      if (data.is_pro && data.verdict) {
        authFetch(`${API}/workspace/history/save`, {
          method: 'POST',
          body: JSON.stringify({ symbol: data.symbol || symbol.trim().toUpperCase(), verdict: data.verdict, confidence: data.confidence || 0 }),
        }).catch((err) => console.error('History save error:', err));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const VerdictIcon = ({ verdict }) => {
    if (verdict === 'BUY') return <TrendingUp className="w-6 h-6 text-emerald-400" />;
    if (verdict === 'SELL') return <TrendingDown className="w-6 h-6 text-red-400" />;
    return <Minus className="w-6 h-6 text-amber-400" />;
  };

  const verdictColor = (v) => {
    if (v === 'BUY') return 'text-emerald-400 bg-emerald-900/30 border-emerald-700/50';
    if (v === 'SELL') return 'text-red-400 bg-red-900/30 border-red-700/50';
    return 'text-amber-400 bg-amber-900/30 border-amber-700/50';
  };

  const miniVerdictColor = (v) => {
    if (v === 'BUY') return 'text-emerald-400';
    if (v === 'SELL') return 'text-red-400';
    if (v === 'ERROR') return 'text-slate-500';
    return 'text-amber-400';
  };

  return (
    <div className="space-y-6" data-testid="ai-hypothesis">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-[#0052FF] to-cyan-500 rounded-xl flex items-center justify-center">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{fontFamily: 'Manrope, sans-serif'}}>AI Investment Hypothesis</h2>
            <p className="text-slate-400 text-xs sm:text-sm">Multi-model analysis using all scraped macro data</p>
          </div>
        </div>
        {isPro && <Badge className="bg-gradient-to-r from-[#0052FF] to-cyan-500 text-white border-0">PRO</Badge>}
      </div>

      {/* Model Selector */}
      <div className="relative" data-testid="model-selector">
        <button
          onClick={() => setShowModelPicker(!showModelPicker)}
          className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-slate-800/70 border border-slate-700/50 rounded-xl hover:border-slate-600 transition-colors"
          data-testid="model-selector-trigger"
        >
          <div className="flex items-center gap-3">
            <div className={`w-8 h-8 rounded-lg ${currentModel.bg} flex items-center justify-center`}>
              <currentModel.icon className={`w-4 h-4 ${currentModel.color}`} />
            </div>
            <div className="text-left">
              <div className="text-white text-sm font-medium flex items-center gap-2">
                {currentModel.label}
                {currentModel.key === 'consensus' && <Badge className="bg-violet-900/50 text-violet-300 border-violet-700/50 text-[9px] px-1.5">3 MODELS</Badge>}
              </div>
              <div className="text-slate-500 text-xs">{currentModel.provider}</div>
            </div>
          </div>
          <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform ${showModelPicker ? 'rotate-180' : ''}`} />
        </button>

        {showModelPicker && (
          <div className="absolute z-50 mt-1 w-full bg-slate-800 border border-slate-700/60 rounded-xl shadow-2xl overflow-hidden" data-testid="model-dropdown">
            {AI_MODELS.map((m) => {
              const ModelIcon = m.icon;
              const locked = !m.free && !isPro;
              return (
                <button
                  key={m.key}
                  onClick={() => {
                    if (locked) return;
                    setSelectedModel(m.key);
                    setShowModelPicker(false);
                  }}
                  className={`w-full flex items-center justify-between gap-3 px-4 py-3 transition-colors ${
                    selectedModel === m.key ? 'bg-[#0052FF]/10 border-l-2 border-[#0052FF]' : 'border-l-2 border-transparent hover:bg-slate-700/40'
                  } ${locked ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}
                  data-testid={`model-option-${m.key}`}
                  disabled={locked}
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-8 h-8 rounded-lg ${m.bg} flex items-center justify-center`}>
                      <ModelIcon className={`w-4 h-4 ${m.color}`} />
                    </div>
                    <div className="text-left">
                      <div className="text-white text-sm font-medium flex items-center gap-2">
                        {m.label}
                        {m.key === 'consensus' && (
                          <Badge className="bg-violet-900/50 text-violet-300 border-violet-700/50 text-[9px] px-1.5">BEST ACCURACY</Badge>
                        )}
                      </div>
                      <div className="text-slate-500 text-xs">{m.provider}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {m.free ? (
                      <Badge className="bg-slate-700/60 text-slate-400 border-slate-600 text-[9px]">FREE</Badge>
                    ) : locked ? (
                      <Lock className="w-4 h-4 text-slate-500" />
                    ) : (
                      <Badge className="bg-[#0052FF]/20 text-[#0052FF] border-[#0052FF]/30 text-[9px]">PRO</Badge>
                    )}
                  </div>
                </button>
              );
            })}
            {!isPro && (
              <div className="px-4 py-2.5 bg-slate-900/60 border-t border-slate-700/40">
                <button onClick={onSubscribe} className="text-[#0052FF] text-xs font-medium hover:underline flex items-center gap-1" data-testid="model-upgrade-btn">
                  <Zap className="w-3 h-3" /> Upgrade to Pro to unlock all models + Consensus Mode
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Search */}
      <form onSubmit={search} className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <Input
            placeholder="Enter ticker (AAPL, BTC, TSLA...)"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
            data-testid="hypothesis-search"
          />
        </div>
        <Button type="submit" disabled={loading || !symbol.trim()} className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl px-6" data-testid="hypothesis-submit">
          {loading ? (selectedModel === 'consensus' ? 'Running 3 Models...' : 'Analyzing...') : 'Analyze'}
        </Button>
      </form>

      {/* Loading */}
      {loading && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-8 text-center">
          <div className="animate-pulse space-y-3">
            <currentModel.icon className={`w-8 h-8 ${currentModel.color} mx-auto animate-spin`} />
            <p className="text-white font-medium">
              {selectedModel === 'consensus'
                ? `Running GPT-5.2, Claude Sonnet 4.5, and Gemini Pro on ${symbol.toUpperCase()}...`
                : `${currentModel.label} is analyzing ${symbol.toUpperCase()}...`}
            </p>
            <p className="text-slate-400 text-sm">
              {selectedModel === 'consensus'
                ? 'Weighted voting across 3 AI models for maximum accuracy'
                : 'Analyzing news, world events, congressional trades, and market data'}
            </p>
          </div>
        </Card>
      )}

      {error && <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg">{error}</div>}

      {/* Locked State (Free User) */}
      {hypothesis && !hypothesis.is_pro && (
        <Card className="relative bg-slate-800/50 border-slate-700/40 rounded-xl overflow-hidden" data-testid="hypothesis-locked">
          <div className="p-6 space-y-4">
            <div className="flex items-center gap-2 text-white font-semibold text-lg">
              <Sparkles className="w-5 h-5 text-[#0052FF]" />
              AI Hypothesis Ready for {hypothesis.symbol}
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-slate-900/60 rounded-lg p-3 text-center">
                <BarChart3 className="w-4 h-4 text-blue-400 mx-auto mb-1" />
                <p className="text-white text-lg font-bold">{hypothesis.teaser.data_sources_count}</p>
                <p className="text-slate-500 text-[10px]">Data Points</p>
              </div>
              <div className="bg-slate-900/60 rounded-lg p-3 text-center">
                <Globe className="w-4 h-4 text-emerald-400 mx-auto mb-1" />
                <p className="text-white text-lg font-bold">{hypothesis.teaser.world_events_count}</p>
                <p className="text-slate-500 text-[10px]">World Events</p>
              </div>
              <div className="bg-slate-900/60 rounded-lg p-3 text-center">
                <Landmark className="w-4 h-4 text-violet-400 mx-auto mb-1" />
                <p className="text-white text-lg font-bold">{hypothesis.teaser.congressional_trades_count}</p>
                <p className="text-slate-500 text-[10px]">Congress Trades</p>
              </div>
            </div>
          </div>
          <div className="relative px-6 pb-6">
            <div className="blur-md select-none pointer-events-none" aria-hidden="true">
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <span className="text-3xl font-bold text-emerald-400">BUY</span>
                  <span className="text-slate-400">|</span>
                  <span className="text-white text-xl font-semibold">Confidence: 78%</span>
                </div>
                <p className="text-slate-300 text-sm">Based on analysis of 19 news articles, 30 world events, 12 congressional trades, and foreign market correlations...</p>
              </div>
            </div>
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-slate-900/50 backdrop-blur-sm rounded-b-xl">
              <Lock className="w-8 h-8 text-[#0052FF] mb-3" />
              <p className="text-white font-semibold text-lg mb-1">Unlock Full AI Hypothesis</p>
              <p className="text-slate-400 text-sm text-center max-w-xs mb-4">{hypothesis.teaser.summary}</p>
              <div className="flex gap-3">
                {!user && (
                  <Button onClick={onLogin} className="bg-slate-700 hover:bg-slate-600 text-white rounded-xl" data-testid="hypothesis-login-btn">
                    Log In
                  </Button>
                )}
                <Button onClick={onSubscribe} className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl" data-testid="hypothesis-subscribe-btn">
                  <Zap className="w-4 h-4 mr-2" /> Subscribe to Pro
                </Button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Full Hypothesis (Pro User) */}
      {hypothesis && hypothesis.is_pro && (
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
                  <Button size="sm" variant="outline" className="border-white/20 text-white/80 hover:bg-white/10 rounded-lg text-[10px] h-7 px-2" onClick={exportReport} disabled={exporting} data-testid="export-report-btn">
                    <Download className="w-3 h-3 mr-1" /> Export
                  </Button>
                </div>
              </div>
            </div>
            {hypothesis.summary && (
              <p className="mt-4 text-sm opacity-90">{hypothesis.summary}</p>
            )}
          </Card>

          {/* Consensus Mode: Individual Model Results */}
          {hypothesis.individual_results?.length > 0 && (
            <Card className="bg-slate-800/50 border-violet-800/30 rounded-xl p-5" data-testid="consensus-breakdown">
              <h3 className="text-violet-400 font-semibold mb-4 flex items-center gap-2">
                <Network className="w-4 h-4" /> Individual Model Verdicts
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {hypothesis.individual_results.map((r) => {
                  const modelDef = AI_MODELS.find(m => m.key === r.model_key) || AI_MODELS[0];
                  const ModelIcon = modelDef.icon;
                  return (
                    <div key={r.model_key} className="bg-slate-900/60 border border-slate-700/40 rounded-xl p-4">
                      <div className="flex items-center gap-2 mb-2">
                        <ModelIcon className={`w-4 h-4 ${modelDef.color}`} />
                        <span className="text-white text-xs font-medium">{r.model}</span>
                      </div>
                      <div className={`text-xl font-black ${miniVerdictColor(r.verdict)}`}>
                        {r.error ? 'FAILED' : r.verdict}
                      </div>
                      <div className="text-slate-500 text-xs mt-1">
                        {r.error ? 'Model error' : `${r.confidence}% confidence`}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          {/* Price Targets */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
              <h3 className="text-slate-400 text-xs font-medium uppercase mb-2">Short-Term Target (1-2 weeks)</h3>
              <p className="text-white text-lg font-bold">{hypothesis.price_target_short || 'N/A'}</p>
            </Card>
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
              <h3 className="text-slate-400 text-xs font-medium uppercase mb-2">Medium-Term Target (1-3 months)</h3>
              <p className="text-white text-lg font-bold">{hypothesis.price_target_medium || 'N/A'}</p>
            </Card>
          </div>

          {/* Thesis */}
          {hypothesis.thesis && (
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
              <h3 className="text-white font-semibold mb-3">Investment Thesis</h3>
              <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-line">{hypothesis.thesis}</p>
            </Card>
          )}

          {/* Catalysts & Risks */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {hypothesis.catalysts?.length > 0 && (
              <Card className="bg-emerald-950/20 border-emerald-800/30 rounded-xl p-5">
                <h3 className="text-emerald-400 font-semibold mb-3 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" /> Key Catalysts
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
              <Card className="bg-red-950/20 border-red-800/30 rounded-xl p-5">
                <h3 className="text-red-400 font-semibold mb-3 flex items-center gap-2">
                  <TrendingDown className="w-4 h-4" /> Key Risks
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
                <h3 className="text-violet-400 font-semibold mb-3 flex items-center gap-2">
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
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
              <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-[#0052FF]" /> Technical Outlook
              </h3>
              <p className="text-slate-300 text-sm">{hypothesis.technical_outlook}</p>
            </Card>
          )}
        </div>
      )}
    </div>
  );
};

export default AIHypothesis;
