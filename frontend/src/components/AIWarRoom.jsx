import React, { useState } from 'react';
import {
  Search, Shield, Zap, Loader2, AlertCircle, Lock
} from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import {
  OverviewCard, AIScoreCard, BriefCard, EarningsCard, InsidersCard,
  ScoreGauge, CompositeBreakdownBar
} from './warroom/WarRoomCards';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const VERDICT_COLORS = {
  'STRONG BUY': { bg: 'bg-emerald-900/40', text: 'text-emerald-400', border: 'border-emerald-700/50' },
  'BUY': { bg: 'bg-emerald-900/30', text: 'text-emerald-400', border: 'border-emerald-700/40' },
  'HOLD': { bg: 'bg-amber-900/30', text: 'text-amber-400', border: 'border-amber-700/40' },
  'SELL': { bg: 'bg-red-900/30', text: 'text-red-400', border: 'border-red-700/40' },
  'STRONG SELL': { bg: 'bg-red-900/40', text: 'text-red-400', border: 'border-red-700/50' },
};

const AIWarRoom = ({ onSubscribe, onLogin }) => {
  const { user, isPro } = useAuth();
  const [symbol, setSymbol] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const analyze = async (e) => {
    e?.preventDefault();
    if (!symbol.trim()) return;
    setLoading(true);
    setError('');
    setData(null);
    try {
      const res = await authFetch(`${API}/intelligence/war-room/${symbol.trim().toUpperCase()}`);
      if (res.status === 403) {
        setError('pro_required');
        return;
      }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Analysis failed');
      }
      setData(await res.json());
    } catch (err) {
      if (error !== 'pro_required') setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const v = data?.composite?.verdict || 'HOLD';
  const vc = VERDICT_COLORS[v] || VERDICT_COLORS['HOLD'];

  return (
    <div className="space-y-6" data-testid="ai-war-room">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-red-600 to-amber-500 rounded-xl flex items-center justify-center">
            <Shield className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>
              AI War Room
            </h2>
            <p className="text-slate-400 text-xs sm:text-sm">Unified command center — AI Score + Earnings + Insider Intelligence</p>
          </div>
        </div>
        {isPro && <Badge className="bg-gradient-to-r from-red-600 to-amber-500 text-white border-0">PRO</Badge>}
      </div>

      {/* Search */}
      <form onSubmit={analyze} className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <Input
            placeholder="Enter ticker symbol (AAPL, TSLA, NVDA...)"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
            data-testid="warroom-search"
          />
        </div>
        <Button type="submit" disabled={loading || !symbol.trim()}
          className="bg-gradient-to-r from-red-600 to-amber-500 hover:from-red-500 hover:to-amber-400 text-white rounded-xl px-6"
          data-testid="warroom-submit">
          {loading ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Scanning...</> : <>Deploy Analysis</>}
        </Button>
      </form>

      {/* Pro Lock */}
      {error === 'pro_required' && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-8 text-center">
          <Lock className="w-10 h-10 text-amber-400 mx-auto mb-3" />
          <h3 className="text-white font-bold text-lg mb-2">War Room is Pro Only</h3>
          <p className="text-slate-400 text-sm mb-4">Get full access to AI-powered multi-signal analysis</p>
          <div className="flex gap-3 justify-center">
            {!user && <Button onClick={onLogin} className="bg-slate-700 hover:bg-slate-600 text-white rounded-xl">Log In</Button>}
            <Button onClick={onSubscribe} className="bg-gradient-to-r from-red-600 to-amber-500 text-white rounded-xl">
              <Zap className="w-4 h-4 mr-2" />Subscribe to Pro
            </Button>
          </div>
        </Card>
      )}

      {/* Loading */}
      {loading && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-8 text-center">
          <Shield className="w-10 h-10 text-amber-400 mx-auto animate-pulse" />
          <p className="text-white font-semibold mt-3">Deploying War Room for {symbol}</p>
          <p className="text-slate-400 text-sm">Scanning earnings, insider trades, technicals, and AI models...</p>
          <div className="flex justify-center gap-6 mt-4">
            {['AI Score', 'Earnings', 'Insiders', 'Technicals', 'Brief'].map((s, i) => (
              <div key={s} className="text-center">
                <div className="w-2 h-2 bg-amber-400 rounded-full mx-auto mb-1 animate-pulse" style={{ animationDelay: `${i * 0.3}s` }} />
                <span className="text-slate-500 text-[10px]">{s}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Error */}
      {error && error !== 'pro_required' && (
        <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />{error}
        </div>
      )}

      {/* Results */}
      {data && (
        <div className="space-y-4">
          {/* Top: Composite Signal + Overview */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card className={`${vc.bg} ${vc.border} border rounded-xl p-5 lg:col-span-1`} data-testid="warroom-verdict">
              <div className="text-center">
                <p className="text-slate-400 text-xs mb-2 uppercase tracking-wider">Composite Signal</p>
                <ScoreGauge score={data.composite.score} label="Conviction" />
                <p className={`text-2xl font-black mt-2 ${vc.text}`}>{v}</p>
                <CompositeBreakdownBar breakdown={data.composite.breakdown} />
              </div>
            </Card>
            <OverviewCard overview={data.overview} symbol={data.symbol} />
          </div>

          {/* Middle: AI Score + Brief */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <AIScoreCard aiScore={data.ai_score} />
            <BriefCard brief={data.brief} />
          </div>

          {/* Bottom: Earnings + Insiders */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <EarningsCard earnings={data.earnings} />
            <InsidersCard insiders={data.insiders} />
          </div>
        </div>
      )}
    </div>
  );
};

export default AIWarRoom;
