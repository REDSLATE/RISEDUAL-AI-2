import React, { useState, useEffect, useRef } from 'react';
import {
  Search, Shield, Zap, Loader2, AlertCircle, Lock
} from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import {
  OverviewCard, EarningsCard, InsidersCard,
  ScoreGauge, CompositeBreakdownBar, CrewInsightsCard
} from './warroom/WarRoomCards';
import AccuracyBadge from './AccuracyBadge';
import { getApiBase } from '../utils/apiBase';
import InfoTooltip from './InfoTooltip';
import { addRecent } from '../utils/recentTickers';

const API = `${getApiBase()}/api`;

const VERDICT_COLORS = {
  'STRONG BUY': { bg: 'bg-lime-600', text: 'text-lime-400', border: 'border-emerald-700/50' },
  'BUY': { bg: 'bg-lime-700', text: 'text-lime-400', border: 'border-emerald-700/40' },
  'HOLD': { bg: 'bg-amber-900/30', text: 'text-amber-300', border: 'border-amber-700/40' },
  'SELL': { bg: 'bg-orange-800', text: 'text-orange-400', border: 'border-red-700/40' },
  'STRONG SELL': { bg: 'bg-orange-700', text: 'text-orange-400', border: 'border-red-700/50' },
};

const AIWarRoom = ({ onSubscribe, onLogin, prefetched }) => {
  const { user, isPro } = useAuth();
  const [symbol, setSymbol] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const analyzeRef = useRef(null);

  // When ``prefetched`` is supplied (the WarRoomHub orchestrates a
  // unified search across all per-symbol tabs), override the
  // component's internal state. Standalone usages (DashboardView)
  // pass nothing and the component fetches on its own as before.
  const isControlled = !!prefetched;
  const effSymbol = isControlled ? (prefetched.symbol || '') : symbol;
  const effData = isControlled ? prefetched.data : data;
  const effLoading = isControlled ? !!prefetched.loading : loading;
  const effError = isControlled ? (prefetched.error || '') : error;

  // Deep-link hook: other surfaces (e.g. Smart Money Board) dispatch
  // `risedualai-warroom` with `detail: TICKER` to jump here AND run the
  // analysis in one click. Disabled when controlled — the hub owns
  // search routing in that mode.
  useEffect(() => {
    if (isControlled) return;
    const handler = (e) => {
      const t = (e?.detail || '').toString().trim().toUpperCase();
      if (!t) return;
      setSymbol(t);
      // Defer so state settles before submit
      setTimeout(() => analyzeRef.current?.(), 50);
    };
    window.addEventListener('risedualai-warroom', handler);
    return () => window.removeEventListener('risedualai-warroom', handler);
  }, [isControlled]);

  const analyze = async (e) => {
    e?.preventDefault();
    if (!symbol.trim()) return;
    setLoading(true);
    setError('');
    setData(null);
    try {
      const res = await authFetch(`${API}/intelligence/war-room/${symbol.trim().toUpperCase()}`);
      if (res.status === 401) throw new Error('Session expired — please log in again.');
      if (res.status === 403) { setError('pro_required'); return; }
      if (res.status === 502 || res.status === 504) throw new Error('Server is busy — please try again in a moment.');
      if (!res.ok) {
        let detail;
        try { detail = (await res.json()).detail; } catch { detail = null; }
        throw new Error(detail || `Server error (${res.status}). Please try again.`);
      }
      setData(await res.json());
      addRecent(symbol);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  analyzeRef.current = analyze;

  const v = effData?.composite?.verdict || 'HOLD';
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
            <InfoTooltip id="ai-war-room" />
            <p className="text-slate-300 text-xs sm:text-sm">Adversarial AI Command Center — Strategist signals, Auditor validates</p>
          </div>
        </div>
        {isPro && (
          <div className="flex items-center gap-2">
            <AccuracyBadge feature="war_room" />
            <Badge className="bg-gradient-to-r from-red-600 to-amber-500 text-white border-0">PRO</Badge>
          </div>
        )}
      </div>

      {/* Search — hidden when controlled by WarRoomHub */}
      {!isControlled && (
        <form onSubmit={analyze} className="flex flex-col sm:flex-row gap-3">
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
      )}

      {/* Pro Lock */}
      {effError === 'pro_required' && (
        <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-8 text-center">
          <Lock className="w-10 h-10 text-amber-300 mx-auto mb-3" />
          <h3 className="text-white font-bold text-lg mb-2">War Room is Pro Only</h3>
          <p className="text-slate-300 text-sm mb-4">Unlock the full Adversarial AI — Strategist + Auditor dual-signal engine</p>
          <div className="flex gap-3 justify-center">
            {!user && <Button onClick={onLogin} className="bg-slate-700 hover:bg-slate-600 text-white rounded-xl">Log In</Button>}
            <Button onClick={onSubscribe} className="bg-gradient-to-r from-red-600 to-amber-500 text-white rounded-xl">
              <Zap className="w-4 h-4 mr-2" />Subscribe to Pro
            </Button>
          </div>
        </Card>
      )}

      {/* Loading */}
      {effLoading && (
        <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-8 text-center">
          <Shield className="w-10 h-10 text-amber-300 mx-auto animate-pulse" />
          <p className="text-white font-semibold mt-3">Deploying War Room for {effSymbol}</p>
          <p className="text-slate-300 text-sm">Running Strategist & Auditor — Adversarial dual-signal analysis in progress...</p>
          <div className="flex justify-center gap-6 mt-4">
            {['Strategist', 'Auditor', 'Data Feeds', 'Retraining', 'Synthesis'].map((s, i) => (
              <div key={s} className="text-center">
                <div className="w-2 h-2 bg-amber-400 rounded-full mx-auto mb-1 animate-pulse" style={{ animationDelay: `${i * 0.3}s` }} />
                <span className="text-slate-400 text-[10px]">{s}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Error */}
      {effError && effError !== 'pro_required' && (
        <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />{effError}
        </div>
      )}

      {/* Empty state when controlled and no search yet */}
      {isControlled && !effLoading && !effError && !effData && (
        <Card className="bg-slate-700/60 border-slate-400/30 rounded-xl p-8 text-center">
          <Shield className="w-10 h-10 text-slate-500 mx-auto mb-3" />
          <p className="text-slate-400 text-sm">Enter a ticker in the unified search above to deploy the War Room</p>
        </Card>
      )}

      {/* Results */}
      {effData && (
        <div className="space-y-4">
          {/* Top: Composite Signal + Overview */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card className={`${vc.bg} ${vc.border} border rounded-xl p-5 lg:col-span-1`} data-testid="warroom-verdict">
              <div className="text-center">
                <p className="text-slate-300 text-xs mb-2 uppercase tracking-wider">Composite Signal</p>
                <ScoreGauge score={effData.composite.score} label="Conviction" />
                <p className={`text-2xl font-black mt-2 ${vc.text}`}>{v}</p>
                <CompositeBreakdownBar breakdown={effData.composite.breakdown} />
              </div>
            </Card>
            <OverviewCard overview={effData.overview} symbol={effData.symbol} />
          </div>

          {/* Middle: Multi-Agent Insights */}
          <CrewInsightsCard composite={effData.composite} />

          {/* Bottom: Earnings + Insiders */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <EarningsCard earnings={effData.earnings} />
            <InsidersCard insiders={effData.insiders} />
          </div>
        </div>
      )}
    </div>
  );
};

export default AIWarRoom;
