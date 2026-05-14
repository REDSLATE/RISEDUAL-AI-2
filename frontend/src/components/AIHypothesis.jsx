import React, { useState, useEffect, useRef } from 'react';
import { Search, Sparkles, Swords, Shield, Eye, Network, Globe } from 'lucide-react';
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
import { exportHypothesisReport } from '../utils/exportHypothesis';
import InfoTooltip from './InfoTooltip';
import { addRecent } from '../utils/recentTickers';

const API = `${getApiBase()}/api`;

// The 4 RISEDUAL brain personas, plus 4-way consensus.
// Free tier: Alpha 1.6. Everything else gates on Pro.
const AI_MODELS = [
  { key: 'alpha',     label: 'Alpha 1.6',     provider: 'Trend follower', icon: Sparkles, color: 'text-emerald-300', bg: 'bg-emerald-700', free: true  },
  { key: 'camaro',    label: 'Camaro 1.3',    provider: 'Challenger',     icon: Swords,   color: 'text-amber-300',   bg: 'bg-amber-700',   free: false },
  { key: 'chevelle',  label: 'Chevelle 1.3',  provider: 'Governor',       icon: Shield,   color: 'text-sky-300',     bg: 'bg-sky-800',     free: false },
  { key: 'redeye',    label: 'RedEye 1.1',    provider: 'Contrary scout', icon: Eye,      color: 'text-rose-300',    bg: 'bg-rose-800',    free: false },
  { key: 'consensus', label: 'Consensus',     provider: 'All 4 brains',   icon: Network,  color: 'text-violet-300',  bg: 'bg-violet-900/30', free: false },
];

const AIHypothesis = ({ onSubscribe, onLogin }) => {
  const { user, isPro } = useAuth();
  const [symbol, setSymbol] = useState('');
  const [hypothesis, setHypothesis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const [selectedModel, setSelectedModel] = useState('alpha');
  const [showModelPicker, setShowModelPicker] = useState(false);
  const searchRef = useRef(null);

  // Deep-link hook: dispatch `risedualai-warroom` with `detail: TICKER` from
  // anywhere in the app to jump here AND auto-run the hypothesis.
  useEffect(() => {
    const handler = (e) => {
      const t = (e?.detail || '').toString().trim().toUpperCase();
      if (!t) return;
      setSymbol(t);
      setTimeout(() => searchRef.current?.(), 50);
    };
    window.addEventListener('risedualai-warroom', handler);
    return () => window.removeEventListener('risedualai-warroom', handler);
  }, []);

  const currentModel = AI_MODELS.find(m => m.key === selectedModel) || AI_MODELS[0];

  const exportReport = () => {
    if (!hypothesis || !hypothesis.is_pro) return;
    setExporting(true);
    try {
      exportHypothesisReport(hypothesis, symbol);
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
      const modelParam = isPro ? selectedModel : 'alpha';
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
      addRecent(symbol);
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
  searchRef.current = search;

  return (
    <div className="space-y-6" data-testid="ai-hypothesis">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-[#3DE8D9] to-cyan-500 rounded-xl flex items-center justify-center">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{fontFamily: 'Manrope, sans-serif'}}>AI Investment Hypothesis</h2>
            <InfoTooltip id="ai-hypothesis" />
            <p className="text-slate-300 text-xs sm:text-sm">4 distinct AI brains — pick one or run them all in Consensus</p>
          </div>
        </div>
        {isPro && (
          <div className="flex items-center gap-2">
            <AccuracyBadge feature="hypothesis" />
            <Badge className="bg-gradient-to-r from-[#3DE8D9] to-cyan-500 text-white border-0">PRO</Badge>
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
      <form onSubmit={search} className="flex flex-col sm:flex-row gap-3">
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
        <Button type="submit" disabled={loading || !symbol.trim()} className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl px-6" data-testid="hypothesis-submit">
          {loading ? (selectedModel === 'consensus' ? 'Running 4 Brains...' : 'Analyzing...') : 'Analyze'}
        </Button>
      </form>

      {/* Loading */}
      {loading && (
        <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-8 text-center">
          <div className="animate-pulse space-y-3">
            <currentModel.icon className={`w-8 h-8 ${currentModel.color} mx-auto animate-spin`} />
            <p className="text-white font-medium">
              {selectedModel === 'consensus'
                ? `Running Alpha 1.6, Camaro 1.3, Chevelle 1.3, and RedEye 1.1 on ${symbol.toUpperCase()}...`
                : `${currentModel.label} is analyzing ${symbol.toUpperCase()}...`}
            </p>
            <p className="text-slate-300 text-sm">
              {selectedModel === 'consensus'
                ? '4-brain consensus: trend, challenger, governor, and contrary scout weighing in together'
                : `${currentModel.label} (${currentModel.provider}) generating thesis with its own voice`}
            </p>
          </div>
        </Card>
      )}

      {error && <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg">{error}</div>}

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
