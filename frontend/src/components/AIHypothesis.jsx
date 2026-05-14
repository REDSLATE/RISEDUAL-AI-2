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
  { key: 'redeye',    label: 'RedEye 1.3',    provider: 'Contrary scout', icon: Eye,      color: 'text-rose-300',    bg: 'bg-rose-800',    free: false },
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
  // SSE progress while a hypothesis is in flight.
  // Shape: { stage, message, brains: [{key,label,verdict,confidence,elapsed_ms,error?}] }
  const [progress, setProgress] = useState(null);
  const searchRef = useRef(null);
  const eventSourceRef = useRef(null);

  // Tear down any open EventSource on unmount.
  useEffect(() => () => {
    if (eventSourceRef.current) {
      try { eventSourceRef.current.close(); } catch { /* ignore */ }
      eventSourceRef.current = null;
    }
  }, []);

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
    setProgress({ stage: 'init', message: 'Starting…', brains: [] });

    const modelParam = isPro ? selectedModel : 'alpha';
    const sym = symbol.trim().toUpperCase();

    // Close any leftover stream.
    if (eventSourceRef.current) {
      try { eventSourceRef.current.close(); } catch { /* ignore */ }
      eventSourceRef.current = null;
    }

    try {
      const url = `${API}/hypothesis/${sym}/stream?model=${modelParam}`;
      const es = new EventSource(url, { withCredentials: true });
      eventSourceRef.current = es;

      const finalize = (data) => {
        setHypothesis(data);
        setProgress(null);
        setLoading(false);
        addRecent(symbol);
        if (data.is_pro && data.verdict) {
          authFetch(`${API}/workspace/history/save`, {
            method: 'POST',
            body: JSON.stringify({
              symbol: data.symbol || sym,
              verdict: data.verdict,
              confidence: data.confidence || 0,
            }),
          }).catch((err) => logger.error('History save error:', err));
        }
        try { es.close(); } catch { /* ignore */ }
        eventSourceRef.current = null;
      };

      es.addEventListener('status', (ev) => {
        try {
          const p = JSON.parse(ev.data);
          setProgress((prev) => ({
            stage: p.stage,
            message: p.message,
            brains: prev?.brains || [],
          }));
        } catch { /* ignore parse */ }
      });

      es.addEventListener('brain_done', (ev) => {
        try {
          const p = JSON.parse(ev.data);
          setProgress((prev) => ({
            stage: prev?.stage || 'brains',
            message: prev?.message || '',
            brains: [
              ...(prev?.brains || []).filter((b) => b.key !== p.brain),
              {
                key: p.brain, label: p.label,
                verdict: p.verdict, confidence: p.confidence,
                elapsed_ms: p.elapsed_ms, error: p.error,
                summary: p.summary,
              },
            ],
          }));
        } catch { /* ignore parse */ }
      });

      es.addEventListener('done', (ev) => {
        try { finalize(JSON.parse(ev.data)); }
        catch (err) { setError('Bad response from server'); setLoading(false); }
      });

      es.addEventListener('error', (ev) => {
        // EventSource fires onerror on both transport drops and explicit
        // error events. Try to parse a friendly payload first.
        let detail = null;
        try { if (ev.data) detail = JSON.parse(ev.data); } catch { /* ignore */ }
        if (detail?.error) {
          setError(detail.error);
        } else if (es.readyState === EventSource.CLOSED) {
          setError('Stream closed unexpectedly. Please retry.');
        } else {
          setError('Connection error — please try again.');
        }
        setLoading(false);
        setProgress(null);
        try { es.close(); } catch { /* ignore */ }
        eventSourceRef.current = null;
      });
    } catch (err) {
      setError(err.message);
      setLoading(false);
      setProgress(null);
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

      {/* Live progress (SSE) */}
      {loading && (
        <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-6 sm:p-8" data-testid="hypothesis-progress">
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <currentModel.icon className={`w-7 h-7 ${currentModel.color} animate-spin`} />
              <div>
                <p className="text-white font-medium">
                  {progress?.message || (selectedModel === 'consensus'
                    ? `Running 4 brains on ${symbol.toUpperCase()}…`
                    : `${currentModel.label} is analyzing ${symbol.toUpperCase()}…`)}
                </p>
                <p className="text-slate-400 text-xs uppercase tracking-wide">
                  stage: {progress?.stage || 'init'}
                </p>
              </div>
            </div>

            {/* Per-brain timeline (consensus mode shows all 4 as they arrive) */}
            {progress?.brains && progress.brains.length > 0 && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2 border-t border-slate-600/40">
                {progress.brains.map((b) => (
                  <div
                    key={b.key}
                    className={`flex items-center justify-between rounded-lg px-3 py-2 ${
                      b.error ? 'bg-rose-900/30 border border-rose-700/40' : 'bg-slate-800/60 border border-slate-600/40'
                    }`}
                    data-testid={`hypothesis-brain-row-${b.key}`}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-white text-sm font-medium truncate">{b.label}</p>
                      <p className={`text-xs ${b.error ? 'text-rose-300' : 'text-slate-400'}`}>
                        {b.error ? 'error' : `${b.verdict} · conf ${Number(b.confidence ?? 0).toFixed(2)}`}
                      </p>
                    </div>
                    <span className="text-[10px] text-slate-500 ml-2 whitespace-nowrap">
                      {b.elapsed_ms != null ? `${(b.elapsed_ms / 1000).toFixed(1)}s` : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
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
