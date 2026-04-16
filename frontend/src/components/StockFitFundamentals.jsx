import React, { useState, useCallback } from 'react';
import { Search, TrendingUp, TrendingDown, DollarSign, Shield, BarChart3, Loader2, FileText, Scale } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { getStockFitFundamentals } from '../services/api';

const fmt = (v, decimals = 1) => {
  if (v == null) return 'N/A';
  const n = Number(v);
  if (isNaN(n)) return 'N/A';
  if (Math.abs(n) >= 1e12) return `$${(n / 1e12).toFixed(decimals)}T`;
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(decimals)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(decimals)}M`;
  return `$${n.toLocaleString()}`;
};

const pct = (v) => {
  if (v == null) return 'N/A';
  return `${(Number(v) * 100).toFixed(1)}%`;
};

const pctRaw = (v) => {
  if (v == null) return 'N/A';
  return `${Number(v).toFixed(1)}%`;
};

const Metric = ({ label, value, sub }) => (
  <div className="bg-slate-800/80 rounded-lg p-3 border border-slate-700/40">
    <p className="text-slate-400 text-[11px] uppercase tracking-wider mb-1">{label}</p>
    <p className="text-white font-semibold text-sm leading-tight">{value}</p>
    {sub && <p className="text-slate-500 text-[10px] mt-0.5">{sub}</p>}
  </div>
);

const ScoreGauge = ({ label, value, max, zone }) => {
  const pctFill = max ? Math.min((value / max) * 100, 100) : 0;
  const color = zone === 'safe' ? '#3DE8D9' : zone === 'grey' ? '#FCD34D' : zone === 'distress' ? '#EF4444' : '#3DE8D9';
  return (
    <div className="bg-slate-800/80 rounded-lg p-4 border border-slate-700/40">
      <div className="flex items-center justify-between mb-2">
        <span className="text-slate-400 text-xs uppercase tracking-wider">{label}</span>
        <span className="text-white font-bold text-lg" style={{ color }}>{value ?? 'N/A'}{max ? `/${max}` : ''}</span>
      </div>
      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pctFill}%`, backgroundColor: color }} />
      </div>
      {zone && <p className="text-[10px] mt-1 capitalize" style={{ color }}>{zone} zone</p>}
    </div>
  );
};

const IncomeTable = ({ data }) => {
  if (!data?.length) return null;
  return (
    <div data-testid="stockfit-income-table">
      <h3 className="text-white font-medium text-sm mb-3 flex items-center gap-2">
        <BarChart3 className="w-4 h-4 text-[#3DE8D9]" />
        Income Statement
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-700/50">
              <th className="text-left text-slate-400 py-2 pr-4 font-medium">Period</th>
              <th className="text-right text-slate-400 py-2 px-2 font-medium">Revenue</th>
              <th className="text-right text-slate-400 py-2 px-2 font-medium">Gross Profit</th>
              <th className="text-right text-slate-400 py-2 px-2 font-medium">Op. Income</th>
              <th className="text-right text-slate-400 py-2 px-2 font-medium">Net Income</th>
              <th className="text-right text-slate-400 py-2 pl-2 font-medium">EPS</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-2 pr-4 text-slate-300 font-medium">{row.period?.slice(0, 4) || '—'}</td>
                <td className="py-2 px-2 text-right text-white">{fmt(row.revenue)}</td>
                <td className="py-2 px-2 text-right text-white">{fmt(row.grossProfit)}</td>
                <td className="py-2 px-2 text-right text-white">{fmt(row.operatingIncome)}</td>
                <td className="py-2 px-2 text-right text-white">{fmt(row.netIncome)}</td>
                <td className="py-2 pl-2 text-right text-[#3DE8D9] font-medium">${row.eps ?? 'N/A'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const BalanceTable = ({ data }) => {
  if (!data?.length) return null;
  return (
    <div data-testid="stockfit-balance-table">
      <h3 className="text-white font-medium text-sm mb-3 flex items-center gap-2">
        <Scale className="w-4 h-4 text-[#3DE8D9]" />
        Balance Sheet
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-700/50">
              <th className="text-left text-slate-400 py-2 pr-4 font-medium">Period</th>
              <th className="text-right text-slate-400 py-2 px-2 font-medium">Total Assets</th>
              <th className="text-right text-slate-400 py-2 px-2 font-medium">Cash</th>
              <th className="text-right text-slate-400 py-2 px-2 font-medium">Total Debt</th>
              <th className="text-right text-slate-400 py-2 px-2 font-medium">Equity</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                <td className="py-2 pr-4 text-slate-300 font-medium">{row.period?.slice(0, 4) || '—'}</td>
                <td className="py-2 px-2 text-right text-white">{fmt(row.assets)}</td>
                <td className="py-2 px-2 text-right text-emerald-400">{fmt(row.cash)}</td>
                <td className="py-2 px-2 text-right text-red-400">{fmt(row.totalDebt)}</td>
                <td className="py-2 px-2 text-right text-white">{fmt(row.totalEquity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default function StockFitFundamentals() {
  const [symbol, setSymbol] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const search = useCallback(async (ticker) => {
    const t = (ticker || symbol).trim().toUpperCase();
    if (!t) return;
    setLoading(true);
    setError('');
    setData(null);
    try {
      const res = await getStockFitFundamentals(t);
      setData(res);
      setSymbol(t);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load fundamentals');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  const quickTickers = ['AAPL', 'NVDA', 'MSFT', 'TSLA', 'GOOGL', 'AMZN', 'META'];

  const scores = data?.scores;
  const earnings = data?.earnings;
  const fScore = scores?.piotroskiFScore;
  const zScore = scores?.altmanZScore;
  const zZone = scores?.zScoreZone;

  return (
    <div data-testid="stockfit-fundamentals" className="space-y-5">
      {/* Search */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <Input
            data-testid="stockfit-search-input"
            placeholder="Ticker (e.g. AAPL)"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && search()}
            className="pl-9 bg-slate-800/60 border-slate-700 text-white placeholder:text-slate-500 h-9 text-sm"
          />
        </div>
        <Button
          data-testid="stockfit-search-btn"
          onClick={() => search()}
          disabled={loading || !symbol.trim()}
          className="bg-[#3DE8D9] hover:bg-[#2fd4c6] text-slate-900 font-medium h-9 px-4 text-sm"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Analyze'}
        </Button>
      </div>

      {/* Quick tickers */}
      <div className="flex flex-wrap gap-1.5">
        {quickTickers.map(t => (
          <button
            key={t}
            onClick={() => { setSymbol(t); search(t); }}
            data-testid={`stockfit-quick-${t}`}
            className="text-[11px] px-2.5 py-1 rounded-full bg-slate-800/60 text-slate-400 hover:text-white hover:bg-slate-700/60 border border-slate-700/40 transition-colors"
          >
            {t}
          </button>
        ))}
      </div>

      {error && (
        <div data-testid="stockfit-error" className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-red-400 text-sm">
          {error}
        </div>
      )}

      {loading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-[#3DE8D9]" />
          <span className="ml-3 text-slate-400 text-sm">Loading SEC fundamentals...</span>
        </div>
      )}

      {data && !loading && (
        <div className="space-y-5 animate-enter">
          {/* Header */}
          <div className="flex items-center gap-3">
            <h2 className="text-white font-semibold text-lg">{data.symbol}</h2>
            <Badge className="bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/20 text-[10px]">
              SEC EDGAR
            </Badge>
          </div>

          {/* Scores + Earnings summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {fScore != null && (
              <ScoreGauge
                label="Piotroski F-Score"
                value={fScore}
                max={9}
                zone={fScore >= 7 ? 'safe' : fScore >= 4 ? 'grey' : 'distress'}
              />
            )}
            {zScore != null && (
              <ScoreGauge label="Altman Z-Score" value={zScore?.toFixed(2)} max={null} zone={zZone} />
            )}
            {earnings?.eps != null && (
              <Metric label="EPS" value={`$${earnings.eps}`} sub={earnings.period?.slice(0, 4)} />
            )}
            {earnings?.revenueGrowth != null && (
              <Metric
                label="Revenue Growth"
                value={pct(earnings.revenueGrowth)}
                sub={Number(earnings.revenueGrowth) > 0 ? 'YoY increase' : 'YoY decline'}
              />
            )}
          </div>

          {/* Margin cards */}
          {earnings && (
            <div className="grid grid-cols-3 gap-3">
              {earnings.grossProfitMargin != null && (
                <Metric label="Gross Margin" value={pctRaw(earnings.grossProfitMargin)} />
              )}
              {earnings.operatingMargin != null && (
                <Metric label="Operating Margin" value={pctRaw(earnings.operatingMargin)} />
              )}
              {earnings.netMargin != null && (
                <Metric label="Net Margin" value={pctRaw(earnings.netMargin)} />
              )}
            </div>
          )}

          {/* Piotroski details */}
          {scores?.piotroskiDetails && (
            <Card className="bg-slate-900/60 border-slate-700/40 p-4">
              <h3 className="text-white font-medium text-sm mb-3 flex items-center gap-2">
                <Shield className="w-4 h-4 text-[#3DE8D9]" />
                Piotroski Checklist ({fScore}/9)
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {Object.entries(scores.piotroskiDetails).map(([key, val]) => (
                  <div key={key} className="flex items-center gap-2 text-xs">
                    <span className={val ? 'text-emerald-400' : 'text-red-400'}>{val ? '+' : '-'}</span>
                    <span className="text-slate-300">{key.replace(/([A-Z])/g, ' $1').replace(/^./, s => s.toUpperCase())}</span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Income Statement */}
          {data.income?.length > 0 && (
            <Card className="bg-slate-900/60 border-slate-700/40 p-4">
              <IncomeTable data={data.income} />
            </Card>
          )}

          {/* Balance Sheet */}
          {data.balanceSheet?.length > 0 && (
            <Card className="bg-slate-900/60 border-slate-700/40 p-4">
              <BalanceTable data={data.balanceSheet} />
            </Card>
          )}

          <p className="text-slate-600 text-[10px] text-center">
            Data sourced from SEC EDGAR via StockFit. Not financial advice.
          </p>
        </div>
      )}

      {/* Empty state */}
      {!data && !loading && !error && (
        <div className="text-center py-12">
          <FileText className="w-10 h-10 text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400 text-sm">Search a ticker to view SEC fundamentals</p>
          <p className="text-slate-600 text-xs mt-1">Income statement, balance sheet, financial health scores</p>
        </div>
      )}
    </div>
  );
}
