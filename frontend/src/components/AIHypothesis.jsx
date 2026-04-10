import React, { useState } from 'react';
import { Search, Lock, Sparkles, Zap, Brain, Cpu, Network, BarChart3, Globe, Landmark } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import ModelSelector from './hypothesis/ModelSelector';
import HypothesisResults from './hypothesis/HypothesisResults';
import HypothesisLocked from './hypothesis/HypothesisLocked';
import AccuracyBadge from './AccuracyBadge';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

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
        `RISEDUAL AI - HYPOTHESIS REPORT`, `${'='.repeat(50)}`,
        `Symbol: ${hypothesis.symbol}`, `Model: ${hypothesis.model || 'GPT-5.2'}`,
        `Generated: ${new Date().toLocaleString()}`, '',
        `VERDICT: ${hypothesis.verdict}`, `Confidence: ${hypothesis.confidence}%`,
        hypothesis.agreement != null ? `Model Agreement: ${hypothesis.agreement}%` : '', '',
        `SUMMARY`, `${'─'.repeat(10)}`, hypothesis.summary || 'N/A', '',
      ];
      if (hypothesis.individual_results?.length) {
        lines.push('INDIVIDUAL MODEL RESULTS', '─'.repeat(25));
        hypothesis.individual_results.forEach(r => lines.push(`${r.model}: ${r.verdict} (${r.confidence}% confidence)`));
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
      if (res.status === 401) throw new Error('Session expired — please log in again.');
      if (res.status === 502 || res.status === 504) throw new Error('Server is busy — please try again in a moment.');
      if (!res.ok) {
        let detail;
        try { detail = (await res.json()).detail; } catch { detail = null; }
        throw new Error(detail || `Server error (${res.status}). Please try again.`);
      }
      const data = await res.json();
      setHypothesis(data);
      if (data.is_pro && data.verdict) {
        authFetch(`${API}/workspace/history/save`, {
          method: 'POST',
          body: JSON.stringify({ symbol: data.symbol || symbol.trim().toUpperCase(), verdict: data.verdict, confidence: data.confidence || 0 }),
        }).catch((err) => logger.error('History save error:', err));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
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
        {isPro && (
          <div className="flex items-center gap-2">
            <AccuracyBadge feature="hypothesis" />
            <Badge className="bg-gradient-to-r from-[#0052FF] to-cyan-500 text-white border-0">PRO</Badge>
          </div>
        )}
      </div>

      {/* Model Selector */}
      <ModelSelector
        models={AI_MODELS}
        selectedModel={selectedModel}
        onSelect={setSelectedModel}
        isPro={isPro}
        onSubscribe={onSubscribe}
        showPicker={showModelPicker}
        setShowPicker={setShowModelPicker}
      />

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
                ? 'Multi-agent crew: Macro Economist, Quant Researcher, and Congressional Tracker collaborating'
                : 'Multi-agent crew analyzing news, world events, congressional trades, and market data'}
            </p>
          </div>
        </Card>
      )}

      {error && <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg">{error}</div>}

      {/* Locked State (Free User) */}
      {hypothesis && !hypothesis.is_pro && (
        <HypothesisLocked hypothesis={hypothesis} user={user} onLogin={onLogin} onSubscribe={onSubscribe} />
      )}

      {/* Full Hypothesis (Pro User) */}
      {hypothesis && hypothesis.is_pro && (
        <HypothesisResults
          hypothesis={hypothesis}
          currentModel={currentModel}
          models={AI_MODELS}
          onExport={exportReport}
          exporting={exporting}
        />
      )}
    </div>
  );
};

export default AIHypothesis;
