import React, { useState, useCallback, useEffect } from 'react';
import { Wand2, Trash2, X, RefreshCw, Lock, Zap, FlaskConical, Store, Layers, Target } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import BacktestResults from './BacktestResults';
import StrategyPreview from './strategy/StrategyPreview';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const EXAMPLE_PROMPTS = [
  "Buy AAPL when RSI drops below 30 and MACD crosses bullish. Sell when RSI hits 70.",
  "Momentum strategy: buy top 3 gainers at market open, sell at close if up 2%+",
  "Mean reversion on SPY using Bollinger Bands — buy at lower band, sell at upper band",
  "Crypto swing trade: buy BTC when it breaks above 50-day SMA with high volume",
  "Iron condor on high-IV stocks during earnings season, 30-delta wings",
];

const StrategyBuilder = ({ onClose, onSubscribe }) => {
  const { user, isPro } = useAuth();
  const [description, setDescription] = useState('');
  const [strategy, setStrategy] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [savedStrategies, setSavedStrategies] = useState([]);
  const [showSaved, setShowSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [expandedSection, setExpandedSection] = useState(null);
  const [backtestSymbol, setBacktestSymbol] = useState('');
  const [backtestYears, setBacktestYears] = useState(3);
  const [backtesting, setBacktesting] = useState(false);
  const [backtestResult, setBacktestResult] = useState(null);
  const [publishing, setPublishing] = useState(false);
  const [published, setPublished] = useState(false);

  const fetchSaved = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/strategy/list`);
      if (res.ok) {
        const data = await res.json();
        setSavedStrategies(data.strategies);
      }
    } catch (e) { logger.error(e); }
  }, []);

  useEffect(() => { fetchSaved(); }, [fetchSaved]);

  const generate = async () => {
    if (!description.trim()) return;
    setLoading(true);
    setError('');
    setStrategy(null);
    try {
      const res = await authFetch(`${API}/strategy/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: description.trim(), model: 'gpt-5.2' }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Generation failed');
      }
      const data = await res.json();
      setStrategy(data.strategy);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const saveStrategy = async () => {
    if (!strategy) return;
    setSaving(true);
    try {
      const res = await authFetch(`${API}/strategy/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy, description }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Save failed');
      }
      fetchSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const deleteStrategy = async (name) => {
    try {
      await authFetch(`${API}/strategy/${encodeURIComponent(name)}`, { method: 'DELETE' });
      fetchSaved();
    } catch (e) { logger.error(e); }
  };

  const runBacktest = async () => {
    if (!strategy || !backtestSymbol.trim()) return;
    setBacktesting(true);
    setBacktestResult(null);
    setPublished(false);
    setError('');
    try {
      const res = await authFetch(`${API}/strategy/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy, symbol: backtestSymbol.trim(), years: backtestYears }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Backtest failed');
      }
      setBacktestResult(await res.json());
    } catch (err) {
      setError(err.message);
    } finally {
      setBacktesting(false);
    }
  };

  const publishToMarketplace = async () => {
    if (!strategy || !backtestResult) return;
    if (!isPro) { onSubscribe(); return; }
    setPublishing(true);
    setError('');
    try {
      const res = await authFetch(`${API}/marketplace/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy,
          description,
          backtest_symbol: backtestResult.symbol,
          backtest_years: backtestYears,
          backtest_metrics: backtestResult.metrics,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Publish failed');
      }
      setPublished(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setPublishing(false);
    }
  };

  const toggle = (section) => setExpandedSection(prev => prev === section ? null : section);

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="strategy-builder">
      <div className="bg-slate-900 rounded-2xl max-w-3xl w-full my-4 border border-slate-400/25">
        {/* Header */}
        <div className="p-6 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-violet-600 to-fuchsia-500 rounded-xl flex items-center justify-center">
              <Wand2 className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-white text-xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>AI Strategy Builder</h2>
              <p className="text-slate-300 text-xs">Describe your strategy in plain English — AI builds the logic</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" className="text-slate-400 border-slate-600 hover:text-white h-8 text-xs" onClick={() => setShowSaved(!showSaved)} data-testid="toggle-saved">
              <Layers className="w-3.5 h-3.5 mr-1" /> Saved ({savedStrategies.length})
            </Button>
            <button onClick={onClose} className="text-slate-400 hover:text-white"><X className="w-5 h-5" /></button>
          </div>
        </div>

        <div className="p-6 space-y-5">
          {/* Saved Strategies Drawer */}
          {showSaved && (
            <SavedStrategiesDrawer
              strategies={savedStrategies}
              onLoad={(s) => { setStrategy(s.strategy); setDescription(s.description); setShowSaved(false); }}
              onDelete={deleteStrategy}
            />
          )}

          {/* Input */}
          <div className="space-y-3">
            <label className="text-slate-300 text-sm font-medium">Describe your trading strategy</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="e.g., Buy AAPL when RSI drops below 30 and MACD crosses bullish. Set a 2% stop loss and take profit at 6%..."
              className="w-full bg-slate-800 border border-slate-400/30/60 rounded-xl px-4 py-3 text-white text-sm placeholder-slate-500 resize-none focus:outline-none focus:border-violet-500/60 min-h-[100px]"
              rows={4}
              data-testid="strategy-input"
            />

            {/* Example prompts */}
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLE_PROMPTS.map((p, idx) => (
                <button
                  key={p}
                  onClick={() => setDescription(p)}
                  className="text-[10px] px-2.5 py-1 rounded-full bg-slate-800/80 text-slate-400 border border-slate-400/25 hover:border-violet-600/50 hover:text-violet-300 transition-colors truncate max-w-[250px]"
                  data-testid={`example-prompt-${idx}`}
                >
                  {p.length > 60 ? p.slice(0, 60) + '...' : p}
                </button>
              ))}
            </div>

            <Button
              onClick={generate}
              disabled={loading || !description.trim()}
              className="w-full bg-gradient-to-r from-violet-600 to-fuchsia-500 hover:from-violet-500 hover:to-fuchsia-400 text-white rounded-xl h-11"
              data-testid="generate-strategy-btn"
            >
              {loading ? (
                <><RefreshCw className="w-4 h-4 mr-2 animate-spin" /> Generating Strategy...</>
              ) : (
                <><Wand2 className="w-4 h-4 mr-2" /> Generate Strategy</>
              )}
            </Button>
          </div>

          {error && <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg">{error}</div>}

          {/* Strategy Results */}
          {strategy && !strategy.error && (
            <>
              <StrategyPreview strategy={strategy} onSave={saveStrategy} saving={saving} expanded={expandedSection} toggle={toggle} />

              {/* Backtest Section */}
              <Card className="bg-gradient-to-r from-cyan-950/30 to-blue-950/30 border-cyan-800/30 rounded-xl p-5" data-testid="backtest-section">
                <div className="flex items-center gap-2 mb-4">
                  <FlaskConical className="w-5 h-5 text-cyan-400" />
                  <h4 className="text-white text-sm font-bold">Backtest This Strategy</h4>
                </div>
                <div className="flex flex-col sm:flex-row gap-3">
                  <div className="flex-1">
                    <label className="text-slate-400 text-[10px] uppercase tracking-wider mb-1 block">Ticker Symbol</label>
                    <input
                      value={backtestSymbol}
                      onChange={e => setBacktestSymbol(e.target.value.toUpperCase())}
                      placeholder="e.g. AAPL, TSLA, SPY"
                      className="w-full bg-slate-800 border border-slate-400/30/60 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-cyan-500/60"
                      data-testid="backtest-symbol-input"
                    />
                  </div>
                  <div className="w-full sm:w-32">
                    <label className="text-slate-400 text-[10px] uppercase tracking-wider mb-1 block">Timeframe</label>
                    <select
                      value={backtestYears}
                      onChange={e => setBacktestYears(Number(e.target.value))}
                      className="w-full bg-slate-800 border border-slate-400/30/60 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-500/60"
                      data-testid="backtest-years-select"
                    >
                      <option value={1}>1 Year</option>
                      <option value={2}>2 Years</option>
                      <option value={3}>3 Years</option>
                      <option value={5}>5 Years</option>
                    </select>
                  </div>
                  <div className="flex items-end">
                    <Button
                      onClick={runBacktest}
                      disabled={backtesting || !backtestSymbol.trim()}
                      className="bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white rounded-lg h-[38px] px-5 text-sm whitespace-nowrap"
                      data-testid="run-backtest-btn"
                    >
                      {backtesting ? (
                        <><RefreshCw className="w-4 h-4 mr-2 animate-spin" /> Running...</>
                      ) : (
                        <><FlaskConical className="w-4 h-4 mr-2" /> Run Backtest</>
                      )}
                    </Button>
                  </div>
                </div>
              </Card>

              {/* Backtest Results */}
              {backtestResult && (
                <>
                  <BacktestResults result={backtestResult} onClose={() => setBacktestResult(null)} />
                  {/* Publish to Marketplace */}
                  <Card className="bg-gradient-to-r from-cyan-950/30 to-blue-950/30 border-cyan-800/30 rounded-xl p-4 flex items-center justify-between flex-wrap gap-3" data-testid="publish-section">
                    <div className="flex items-center gap-2">
                      <Store className="w-5 h-5 text-cyan-400" />
                      <div>
                        <p className="text-white text-sm font-semibold">Share with the community</p>
                        <p className="text-slate-400 text-[10px]">Publish this backtested strategy to the Marketplace</p>
                      </div>
                    </div>
                    {published ? (
                      <Badge className="bg-lime-600 text-lime-400 border-emerald-700/50 text-xs px-3 py-1">Published</Badge>
                    ) : (
                      <Button
                        onClick={publishToMarketplace}
                        disabled={publishing}
                        className={`text-xs h-8 rounded-lg px-4 ${isPro ? 'bg-cyan-600 hover:bg-cyan-500 text-white' : 'bg-slate-700 text-slate-400'}`}
                        data-testid="publish-marketplace-btn"
                      >
                        {!isPro ? (
                          <><Lock className="w-3 h-3 mr-1" /> Pro Only</>
                        ) : publishing ? (
                          'Publishing...'
                        ) : (
                          <><Store className="w-3.5 h-3.5 mr-1" /> Publish to Marketplace</>
                        )}
                      </Button>
                    )}
                  </Card>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

const SavedStrategiesDrawer = ({ strategies, onLoad, onDelete }) => (
  <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="saved-strategies">
    <h4 className="text-white text-sm font-semibold mb-3">Saved Strategies</h4>
    {strategies.length === 0 ? (
      <div className="text-center py-6" data-testid="no-saved-strategies">
        <div className="w-10 h-10 bg-slate-800/50 rounded-xl flex items-center justify-center mx-auto mb-2 border border-slate-400/30/30">
          <Target className="w-5 h-5 text-slate-400" />
        </div>
        <p className="text-slate-300 text-xs">No saved strategies yet.</p>
        <p className="text-slate-400 text-[10px]">Generate and save your first one above.</p>
      </div>
    ) : (
      <div className="space-y-2 max-h-[200px] overflow-y-auto">
        {strategies.map((s) => (
          <div key={s.name} className="flex items-center justify-between bg-slate-800/50 border border-slate-400/30/30 rounded-lg px-3 py-2.5 group">
            <button className="flex-1 text-left" onClick={() => onLoad(s)} data-testid={`load-strategy-${s.name}`}>
              <span className="text-white text-sm font-medium">{s.strategy?.name || s.name}</span>
              <p className="text-slate-400 text-[10px] truncate max-w-[300px]">{s.description}</p>
            </button>
            <button onClick={() => onDelete(s.name)} className="text-slate-400 hover:text-orange-400 opacity-0 group-hover:opacity-100 transition-all" data-testid={`delete-strategy-${s.name}`}>
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    )}
  </Card>
);

export default StrategyBuilder;
