import React, { useState, useCallback, useEffect } from 'react';
import { Wand2, Save, Trash2, ChevronDown, ChevronUp, AlertTriangle, TrendingUp, TrendingDown, Shield, Clock, Target, Layers, X, RefreshCw, Sparkles, Lock, Zap, FlaskConical } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import BacktestResults from './BacktestResults';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

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

  const fetchSaved = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/strategy/list`);
      if (res.ok) {
        const data = await res.json();
        setSavedStrategies(data.strategies);
      }
    } catch (e) { console.error(e); }
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
    } catch (e) { console.error(e); }
  };

  const runBacktest = async () => {
    if (!strategy || !backtestSymbol.trim()) return;
    setBacktesting(true);
    setBacktestResult(null);
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

  const toggle = (section) => setExpandedSection(prev => prev === section ? null : section);

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="strategy-builder">
      <div className="bg-slate-900 rounded-2xl max-w-3xl w-full my-4 border border-slate-700/50">
        {/* Header */}
        <div className="p-6 border-b border-slate-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-violet-600 to-fuchsia-500 rounded-xl flex items-center justify-center">
              <Wand2 className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-white text-xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>AI Strategy Builder</h2>
              <p className="text-slate-400 text-xs">Describe your strategy in plain English — AI builds the logic</p>
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
              className="w-full bg-slate-800 border border-slate-700/60 rounded-xl px-4 py-3 text-white text-sm placeholder-slate-500 resize-none focus:outline-none focus:border-violet-500/60 min-h-[100px]"
              rows={4}
              data-testid="strategy-input"
            />

            {/* Example prompts */}
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLE_PROMPTS.map((p, i) => (
                <button
                  key={i}
                  onClick={() => setDescription(p)}
                  className="text-[10px] px-2.5 py-1 rounded-full bg-slate-800/80 text-slate-400 border border-slate-700/50 hover:border-violet-600/50 hover:text-violet-300 transition-colors truncate max-w-[250px]"
                  data-testid={`example-prompt-${i}`}
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

          {error && <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg">{error}</div>}

          {/* Strategy Results */}
          {strategy && !strategy.error && (
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
                  <Button size="sm" onClick={saveStrategy} disabled={saving} className="bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-xs h-8" data-testid="save-strategy-btn">
                    <Save className="w-3.5 h-3.5 mr-1" /> {saving ? 'Saving...' : 'Save Strategy'}
                  </Button>
                  <Badge className="bg-slate-800 text-slate-400 border-slate-700 text-[9px]">
                    <Sparkles className="w-3 h-3 mr-1" /> {strategy.model_used || 'GPT-5.2'}
                  </Badge>
                </div>
              </Card>

              {/* Indicators */}
              {strategy.indicators?.length > 0 && (
                <CollapsibleSection title="Technical Indicators" icon={<Target className="w-4 h-4 text-blue-400" />} section="indicators" expanded={expandedSection} toggle={toggle}>
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
                <CollapsibleSection title="Entry Rules" icon={<TrendingUp className="w-4 h-4 text-emerald-400" />} section="entry" expanded={expandedSection} toggle={toggle}>
                  <div className="space-y-2">
                    {strategy.entry_rules.map((rule, i) => (
                      <RuleCard key={`entry-${i}`} rule={rule} index={i} color="emerald" />
                    ))}
                  </div>
                </CollapsibleSection>
              )}

              {/* Exit Rules */}
              {strategy.exit_rules?.length > 0 && (
                <CollapsibleSection title="Exit Rules" icon={<TrendingDown className="w-4 h-4 text-red-400" />} section="exit" expanded={expandedSection} toggle={toggle}>
                  <div className="space-y-2">
                    {strategy.exit_rules.map((rule, i) => (
                      <RuleCard key={`exit-${i}`} rule={rule} index={i} color="red" />
                    ))}
                  </div>
                </CollapsibleSection>
              )}

              {/* Risk Management */}
              {strategy.risk_management && (
                <CollapsibleSection title="Risk Management" icon={<Shield className="w-4 h-4 text-amber-400" />} section="risk" expanded={expandedSection} toggle={toggle}>
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
                      <Clock className="w-4 h-4 text-[#0052FF]" />
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
          )}
        </div>
      </div>
    </div>
  );
};

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
      {expanded === section ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
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

const SavedStrategiesDrawer = ({ strategies, onLoad, onDelete }) => (
  <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-4" data-testid="saved-strategies">
    <h4 className="text-white text-sm font-semibold mb-3">Saved Strategies</h4>
    {strategies.length === 0 ? (
      <p className="text-slate-500 text-xs">No saved strategies yet. Generate and save your first one.</p>
    ) : (
      <div className="space-y-2 max-h-[200px] overflow-y-auto">
        {strategies.map((s) => (
          <div key={s.name} className="flex items-center justify-between bg-slate-900/60 border border-slate-700/30 rounded-lg px-3 py-2.5 group">
            <button className="flex-1 text-left" onClick={() => onLoad(s)} data-testid={`load-strategy-${s.name}`}>
              <span className="text-white text-sm font-medium">{s.strategy?.name || s.name}</span>
              <p className="text-slate-500 text-[10px] truncate max-w-[300px]">{s.description}</p>
            </button>
            <button onClick={() => onDelete(s.name)} className="text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all" data-testid={`delete-strategy-${s.name}`}>
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    )}
  </Card>
);

export default StrategyBuilder;
