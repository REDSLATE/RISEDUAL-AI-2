import React, { useState } from 'react';
import { PieChart, Plus, Trash2, Zap, TrendingUp, TrendingDown, Minus, Shield, AlertTriangle, Lock } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const gradeColor = (g) => ({ A: 'text-lime-400 bg-lime-700', B: 'text-blue-400 bg-blue-900/30', C: 'text-amber-300 bg-amber-900/30', D: 'text-orange-400 bg-orange-800', F: 'text-orange-400 bg-orange-800' }[g] || 'text-slate-400 bg-slate-800');
const riskColor = (r) => ({ low: 'text-lime-400', medium: 'text-amber-300', high: 'text-orange-400', critical: 'text-orange-400' }[r] || 'text-slate-400');

const PortfolioAnalyzer = ({ onClose, onSubscribe }) => {
  const { isPro } = useAuth();
  const [holdings, setHoldings] = useState([{ ticker: '', shares: '', avg_price: '' }]);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const updateHolding = (i, field, value) => {
    setHoldings(prev => prev.map((h, idx) => idx === i ? { ...h, [field]: value } : h));
  };

  const addRow = () => setHoldings(prev => [...prev, { ticker: '', shares: '', avg_price: '' }]);
  const removeRow = (i) => setHoldings(prev => prev.filter((_, idx) => idx !== i));

  const analyze = async () => {
    if (!isPro) { onSubscribe?.(); return; }
    const valid = holdings.filter(h => h.ticker && h.shares && h.avg_price);
    if (!valid.length) { setError('Add at least one holding'); return; }
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API}/portfolio/analyze`, {
        method: 'POST',
        body: JSON.stringify({ holdings: valid.map(h => ({ ticker: h.ticker.toUpperCase(), shares: parseFloat(h.shares), avg_price: parseFloat(h.avg_price) })) }),
      });
      if (res.status === 401) throw new Error('Session expired — please log in again.');
      if (res.status === 403) { onSubscribe?.(); return; }
      if (res.status === 502 || res.status === 504) throw new Error('Server is busy — please try again.');
      if (!res.ok) {
        let detail;
        try { detail = (await res.json()).detail; } catch { detail = null; }
        throw new Error(detail || `Server error (${res.status}). Please try again.`);
      }
      setResult(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="portfolio-analyzer">
      <div className="bg-slate-900 rounded-2xl max-w-2xl w-full my-4 border border-slate-400/25">
        <div className="p-5 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-[#3DE8D9] to-cyan-500 rounded-xl flex items-center justify-center">
              <PieChart className="w-5 h-5 text-white" />
            </div>
            <div>
              <h2 className="text-white text-lg font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>Portfolio Analyzer</h2>
              <p className="text-slate-300 text-xs">AI-powered health score & rebalancing</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge className="bg-gradient-to-r from-[#3DE8D9] to-cyan-500 text-white border-0 text-xs">PRO</Badge>
            <button onClick={onClose} className="text-slate-400 hover:text-white text-xl px-2">×</button>
          </div>
        </div>

        <div className="p-5 space-y-4">
          {!isPro && (
            <div className="bg-slate-800/60 border border-slate-400/25 rounded-xl p-4 text-center">
              <Lock className="w-8 h-8 text-slate-400 mx-auto mb-2" />
              <p className="text-white font-semibold text-sm mb-1">Pro Feature</p>
              <p className="text-slate-300 text-xs mb-3">Input your holdings and get an AI health score, risk analysis, and rebalancing suggestions.</p>
              <Button className="bg-[#3DE8D9] text-white rounded-xl" onClick={onSubscribe}>Upgrade to Pro</Button>
            </div>
          )}

          {isPro && !result && (
            <>
              <div className="space-y-2">
                {holdings.map((h, i) => (
                  <div key={`holding-${i}-${h.ticker}`} className="flex gap-2 items-center">
                    <Input placeholder="Ticker" value={h.ticker} onChange={e => updateHolding(i, 'ticker', e.target.value.toUpperCase())} className="bg-slate-800 border-slate-600 text-white rounded-xl w-24" />
                    <Input placeholder="Shares" type="number" value={h.shares} onChange={e => updateHolding(i, 'shares', e.target.value)} className="bg-slate-800 border-slate-600 text-white rounded-xl w-24" />
                    <Input placeholder="Avg Price" type="number" value={h.avg_price} onChange={e => updateHolding(i, 'avg_price', e.target.value)} className="bg-slate-800 border-slate-600 text-white rounded-xl flex-1" />
                    {holdings.length > 1 && (
                      <button onClick={() => removeRow(i)} className="text-slate-400 hover:text-orange-400"><Trash2 className="w-4 h-4" /></button>
                    )}
                  </div>
                ))}
              </div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={addRow} className="border-slate-600 text-slate-300 rounded-xl"><Plus className="w-3 h-3 mr-1" /> Add Holding</Button>
                <Button onClick={analyze} disabled={loading} className="bg-[#3DE8D9] text-white rounded-xl flex-1">
                  {loading ? 'Analyzing...' : 'Analyze Portfolio'}
                </Button>
              </div>
              {error && <p className="text-orange-400 text-xs">{error}</p>}
            </>
          )}

          {result && (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3">
                <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-3 text-center">
                  <p className="text-3xl font-bold text-white">{result.health_score}</p>
                  <p className="text-slate-300 text-xs">Health Score</p>
                </Card>
                <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-3 text-center">
                  <p className={`text-lg font-bold ${riskColor(result.risk_level)}`}>{result.risk_level?.toUpperCase()}</p>
                  <p className="text-slate-300 text-xs">Risk Level</p>
                </Card>
                <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-3 text-center">
                  <span className={`text-2xl font-bold px-3 py-1 rounded-lg ${gradeColor(result.diversification_grade)}`}>{result.diversification_grade}</span>
                  <p className="text-slate-300 text-xs mt-1">Diversification</p>
                </Card>
              </div>

              {result.summary && <p className="text-slate-300 text-sm bg-slate-700/60 rounded-xl p-3 border border-slate-400/30/30">{result.summary}</p>}

              {result.suggestions?.length > 0 && (
                <div>
                  <h4 className="text-white text-sm font-semibold mb-2 flex items-center gap-1"><Zap className="w-4 h-4 text-amber-300" /> Suggestions</h4>
                  <ul className="space-y-1">
                    {result.suggestions.map((s, i) => <li key={`sug-${i}`} className="text-slate-300 text-xs flex items-start gap-2"><span className="text-[#3DE8D9] mt-0.5">•</span>{s}</li>)}
                  </ul>
                </div>
              )}

              {result.rebalance_actions?.length > 0 && (
                <div>
                  <h4 className="text-white text-sm font-semibold mb-2 flex items-center gap-1"><Shield className="w-4 h-4 text-blue-400" /> Rebalance Actions</h4>
                  <div className="space-y-1">
                    {result.rebalance_actions.map((a, i) => (
                      <div key={`reb-${i}`} className="flex items-center gap-2 bg-slate-700/60 rounded-lg px-3 py-2 text-xs">
                        {a.action === 'buy' ? <TrendingUp className="w-3.5 h-3.5 text-lime-400" /> : a.action === 'sell' ? <TrendingDown className="w-3.5 h-3.5 text-orange-400" /> : <Minus className="w-3.5 h-3.5 text-amber-300" />}
                        <span className="text-white font-semibold">{a.ticker}</span>
                        <Badge className={`text-[10px] border ${a.action === 'buy' ? 'text-lime-400 bg-lime-700 border-emerald-700/50' : a.action === 'sell' ? 'text-orange-400 bg-orange-800 border-red-700/50' : 'text-amber-300 bg-amber-900/30 border-amber-700/50'}`}>{a.action?.toUpperCase()}</Badge>
                        <span className="text-slate-400 flex-1">{a.reason}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <Button variant="outline" onClick={() => setResult(null)} className="border-slate-600 text-slate-300 rounded-xl w-full">Analyze Again</Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PortfolioAnalyzer;
