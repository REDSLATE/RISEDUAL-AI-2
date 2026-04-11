import React, { useState, useCallback } from 'react';
import { Brain, RefreshCw, TrendingUp, TrendingDown, AlertTriangle, Gauge, Target, Activity, ChevronDown, Minus, ArrowUp, ArrowDown, Shield } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

// Score/severity helper functions to eliminate nested ternaries
const scoreGradient = (score) =>
  score >= 70 ? 'from-emerald-600 to-green-600' :
  score >= 40 ? 'from-amber-600 to-yellow-600' : 'from-red-600 to-rose-600';

const severityBadge = (sev) =>
  sev === 'high' ? 'bg-red-500/40 text-orange-300' :
  sev === 'medium' ? 'bg-amber-800/50 text-amber-300' : 'bg-slate-700 text-slate-400';

const tickerScoreColor = (score) =>
  score >= 7 ? 'text-lime-400 border-emerald-500' :
  score >= 4 ? 'text-amber-300 border-amber-500' : 'text-orange-400 border-red-500';

const verdictBadge = (v) =>
  v === 'buy' ? 'bg-lime-600 text-lime-400' :
  v === 'sell' ? 'bg-orange-700 text-orange-400' : 'bg-amber-900/40 text-amber-300';

const WatchlistIntelligence = ({ onSubscribe }) => {
  const { user, isPro } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);

  const generate = useCallback(async (refresh = false) => {
    if (!user) return;
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API}/intelligence/watchlist?refresh=${refresh}`);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Failed to generate');
      }
      setData(await res.json());
      setExpanded(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [user]);

  if (!user) return null;

  return (
    <Card className="bg-slate-900/80 border-slate-400/25 rounded-2xl overflow-hidden" data-testid="watchlist-intelligence">
      {/* Header */}
      <button
        onClick={() => data ? setExpanded(e => !e) : generate(false)}
        className="w-full flex items-center justify-between p-4 sm:p-5 text-left"
        data-testid="wl-intel-toggle"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-gradient-to-br from-violet-600 to-indigo-600 rounded-xl flex items-center justify-center">
            <Brain className="w-5 h-5 text-white" />
          </div>
          <div>
            <h3 className="text-white text-sm sm:text-base font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>Watchlist Intelligence</h3>
            <p className="text-slate-400 text-[10px] sm:text-xs">
              {data ? `Last updated ${new Date(data.generated_at).toLocaleString()}` : 'AI-powered analysis of your watchlist'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data?.summary?.health_score !== undefined && (
            <HealthBadge score={data.summary.health_score} />
          )}
          <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {/* Content */}
      {expanded && (
        <div className="px-4 sm:px-5 pb-5 space-y-4">
          {!data && !loading && !error && (
            <div className="text-center py-8">
              <Brain className="w-10 h-10 text-slate-700 mx-auto mb-3" />
              <p className="text-slate-300 text-sm mb-3">Analyze your entire watchlist with one click</p>
              <Button
                onClick={() => generate(false)}
                className="bg-gradient-to-r from-violet-600 to-indigo-600 text-white rounded-xl px-6"
                data-testid="wl-intel-generate-btn"
              >
                <Brain className="w-4 h-4 mr-2" /> Generate Intelligence
              </Button>
            </div>
          )}

          {loading && (
            <div className="text-center py-8">
              <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
              <p className="text-slate-300 text-sm">Analyzing your watchlist...</p>
              <p className="text-slate-400 text-[10px] mt-1">Fetching quotes, computing technicals, running AI</p>
            </div>
          )}

          {error && (
            <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg">{error}</div>
          )}

          {data && !loading && (
            <>
              {/* Summary */}
              <SummaryCard summary={data.summary} />

              {/* Alerts */}
              {data.alerts?.length > 0 && <AlertsList alerts={data.alerts} />}

              {/* Top Movers */}
              {data.top_movers?.length > 0 && <TopMovers movers={data.top_movers} />}

              {/* Per-Ticker Cards */}
              {data.tickers?.length > 0 && <TickerGrid tickers={data.tickers} />}

              {/* Refresh */}
              <div className="flex justify-center pt-2">
                <Button
                  onClick={() => generate(true)}
                  disabled={loading}
                  variant="outline"
                  className="text-slate-400 border-slate-400/30 hover:text-white text-xs rounded-xl"
                  data-testid="wl-intel-refresh-btn"
                >
                  <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} /> Refresh Analysis
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  );
};


const HealthBadge = ({ score }) => {
  const color = scoreGradient(score);
  return (
    <div className={`bg-gradient-to-r ${color} text-white text-[10px] font-bold px-2.5 py-1 rounded-lg flex items-center gap-1`} data-testid="wl-health-score">
      <Gauge className="w-3 h-3" />{score}
    </div>
  );
};


const SummaryCard = ({ summary }) => {
  if (!summary) return null;
  return (
    <Card className="bg-gradient-to-r from-violet-950/50 to-indigo-950/40 border-violet-800/30 rounded-xl p-4" data-testid="wl-summary">
      <h4 className="text-white text-sm font-bold mb-1">{summary.headline}</h4>
      <p className="text-slate-300 text-xs mb-3">{summary.outlook}</p>
      <div className="flex gap-3 flex-wrap">
        <MiniStat label="Bullish" value={summary.bullish_count || 0} color="text-lime-400" />
        <MiniStat label="Bearish" value={summary.bearish_count || 0} color="text-orange-400" />
        <MiniStat label="Neutral" value={summary.neutral_count || 0} color="text-amber-300" />
      </div>
    </Card>
  );
};

const MiniStat = ({ label, value, color }) => (
  <div className="flex items-center gap-1.5">
    <span className={`text-lg font-bold ${color}`}>{value}</span>
    <span className="text-slate-400 text-[10px]">{label}</span>
  </div>
);


const AlertsList = ({ alerts }) => (
  <div className="space-y-2" data-testid="wl-alerts">
    <p className="text-slate-400 text-[10px] uppercase tracking-wider font-medium flex items-center gap-1.5">
      <AlertTriangle className="w-3.5 h-3.5 text-amber-300" /> Alerts
    </p>
    {alerts.map((a, i) => {
      const severityColors = {
        high: 'bg-orange-800 border-orange-700/40 text-orange-300',
        medium: 'bg-amber-900/30 border-amber-800/40 text-amber-300',
        low: 'bg-slate-800/60 border-slate-400/30/40 text-slate-300',
      };
      const style = severityColors[a.severity] || severityColors.low;
      return (
        <div key={`alert-${i}`} className={`flex items-center gap-3 p-3 rounded-lg border ${style}`} data-testid={`wl-alert-${i}`}>
          <Badge className="bg-slate-700/60 text-white text-[9px] font-bold shrink-0">{a.symbol}</Badge>
          <span className="text-xs flex-1">{a.message}</span>
          <Badge className={`text-[8px] capitalize ${severityBadge(a.severity)}`}>
            {a.severity}
          </Badge>
        </div>
      );
    })}
  </div>
);


const TopMovers = ({ movers }) => (
  <div data-testid="wl-top-movers">
    <p className="text-slate-400 text-[10px] uppercase tracking-wider font-medium mb-2 flex items-center gap-1.5">
      <Activity className="w-3.5 h-3.5 text-cyan-400" /> Top Movers
    </p>
    <div className="flex gap-2 flex-wrap">
      {movers.map((m, i) => {
        const positive = (m.change_pct || 0) >= 0;
        return (
          <Card key={`mover-${i}`} className={`flex items-center gap-2 px-3 py-2 rounded-lg border ${positive ? 'bg-green-600 border-lime-700/30' : 'bg-red-500/20 border-orange-700/30'}`}>
            <span className="text-white text-xs font-bold">{m.symbol}</span>
            <span className={`text-xs font-semibold ${positive ? 'text-lime-400' : 'text-orange-400'}`}>
              {positive ? '+' : ''}{m.change_pct?.toFixed(1)}%
            </span>
            {m.reason && <span className="text-slate-400 text-[9px] hidden sm:inline">— {m.reason}</span>}
          </Card>
        );
      })}
    </div>
  </div>
);


const TickerGrid = ({ tickers }) => (
  <div data-testid="wl-ticker-grid">
    <p className="text-slate-400 text-[10px] uppercase tracking-wider font-medium mb-2 flex items-center gap-1.5">
      <Target className="w-3.5 h-3.5 text-violet-300" /> Ticker Scores
    </p>
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
      {tickers.map((t, i) => {
        const q = t.quote || {};
        const tech = t.technicals || {};
        const verdictColors = { buy: 'text-lime-400', hold: 'text-amber-300', sell: 'text-orange-400' };
        const scoreColor = tickerScoreColor(t.score);
        const changePct = q.change_pct || 0;

        return (
          <Card key={`ticker-${i}`} className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-3 flex items-center gap-3" data-testid={`wl-ticker-${t.symbol}`}>
            {/* Score Circle */}
            <div className={`w-10 h-10 rounded-full border-2 flex items-center justify-center shrink-0 ${scoreColor}`}>
              <span className={`text-sm font-black ${scoreColor.split(' ')[0]}`}>{t.score}</span>
            </div>

            {/* Info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-white text-sm font-bold">{t.symbol}</span>
                <Badge className={`text-[8px] uppercase font-bold px-1.5 ${verdictBadge(t.verdict)}`}>
                  {t.verdict}
                </Badge>
              </div>
              <p className="text-slate-400 text-[10px] truncate">{t.one_liner}</p>
            </div>

            {/* Price */}
            <div className="text-right shrink-0">
              <p className="text-white text-xs font-semibold">${q.price?.toFixed(2) || '—'}</p>
              <p className={`text-[10px] font-medium ${changePct >= 0 ? 'text-lime-400' : 'text-orange-400'}`}>
                {changePct >= 0 ? '+' : ''}{changePct.toFixed(1)}%
              </p>
            </div>
          </Card>
        );
      })}
    </div>
  </div>
);

export default WatchlistIntelligence;
