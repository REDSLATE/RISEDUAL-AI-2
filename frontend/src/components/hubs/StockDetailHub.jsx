import React, { useState } from 'react';
import { Search, Building2, FileText, Users } from 'lucide-react';
import { Input } from '../ui/input';
import { Button } from '../ui/button';
import CompanyResearch from '../CompanyResearch';

const StockFitFundamentals = React.lazy(() => import('../StockFitFundamentals'));
const StockFit13F = React.lazy(() => import('../StockFit13F'));

const TABS = [
  { key: 'overview',     label: 'Overview',     icon: Building2, desc: 'AI company research with cited sources' },
  { key: 'fundamentals', label: 'Fundamentals', icon: FileText,  desc: 'SEC EDGAR — Income, Balance Sheet, F-Score, Z-Score' },
  { key: 'holders',      label: '13F Holders',  icon: Users,     desc: 'Institutional positions & quarterly changes' },
];

/**
 * StockDetailHub — per-ticker drill.
 *
 * Replaces three standalone Research tabs (Company · StockFit · 13F Holders) with
 * one unified surface that shares a single ticker input across all three.
 *
 * The shared input dispatches the existing `risedualai-research` event, which
 * CompanyResearch already listens for. StockFitFundamentals and StockFit13F
 * were wired to the same event in this phase so a single search populates all
 * three tabs at once.
 */
export default function StockDetailHub({ initialTab, initialSymbol }) {
  const [tab, setTab] = useState(initialTab || 'overview');
  const [symbol, setSymbol] = useState((initialSymbol || '').toUpperCase());
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  const runSearch = React.useCallback((raw) => {
    const t = (raw || symbol || '').trim().toUpperCase();
    if (!t) return;
    setSymbol(t);
    window.dispatchEvent(new CustomEvent('risedualai-research', { detail: t }));
  }, [symbol]);

  React.useEffect(() => {
    if (initialSymbol) runSearch(initialSymbol);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSymbol]);

  const quickTickers = ['AAPL', 'NVDA', 'MSFT', 'TSLA', 'GOOGL', 'AMZN', 'META'];
  const active = TABS.find(t => t.key === tab) || TABS[0];

  return (
    <div data-testid="stock-detail-hub" className="animate-enter">
      {/* Header */}
      <div className="mb-5">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-7 h-7 rounded-lg bg-[#3DE8D9]/10 border border-[#3DE8D9]/30 flex items-center justify-center">
            <Building2 className="w-4 h-4 text-[#3DE8D9]" />
          </div>
          <h1 className="text-white text-xl sm:text-2xl font-bold tracking-tight">Stock Detail</h1>
          {symbol && (
            <span className="ml-1 text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-[#3DE8D9]/15 text-[#3DE8D9] border border-[#3DE8D9]/30">
              {symbol}
            </span>
          )}
        </div>
        <p className="text-slate-400 text-xs sm:text-sm">{active.desc}</p>
      </div>

      {/* Shared ticker input — broadcasts to all three sub-tabs */}
      <form
        onSubmit={e => { e.preventDefault(); runSearch(); }}
        className="flex items-center gap-2 mb-4"
      >
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <Input
            data-testid="stock-detail-search-input"
            placeholder="Ticker (AAPL, NVDA, MSFT…)"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            className="pl-9 bg-slate-800/60 border-slate-700 text-white placeholder:text-slate-500 h-9 text-sm"
          />
        </div>
        <Button
          type="submit"
          disabled={!symbol.trim()}
          data-testid="stock-detail-search-btn"
          className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-slate-900 h-9 text-sm font-semibold"
        >
          Research
        </Button>
      </form>

      {/* Quick tickers */}
      <div className="flex flex-wrap items-center gap-1.5 mb-5">
        <span className="text-[10px] text-slate-500 uppercase tracking-wider mr-1">Quick:</span>
        {quickTickers.map(t => (
          <button
            key={t}
            onClick={() => runSearch(t)}
            className="px-2 py-0.5 rounded-md bg-slate-800/60 hover:bg-slate-700/60 border border-slate-700/40 text-slate-300 hover:text-[#3DE8D9] text-[11px] font-medium transition-colors"
            data-testid={`stock-detail-quick-${t}`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Sub-nav tabs */}
      <div className="flex items-center gap-1 overflow-x-auto pb-1 mb-5 border-b border-slate-700/50 scrollbar-hide">
        {TABS.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-t-lg text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                tab === t.key
                  ? 'bg-slate-800 text-[#3DE8D9] border-b-2 border-[#3DE8D9]'
                  : 'text-slate-400 hover:text-white'
              }`}
              data-testid={`stockdetail-tab-${t.key}`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Active surface */}
      <React.Suspense fallback={fallback}>
        <div className="animate-enter">
          {tab === 'overview'     && <CompanyResearch />}
          {tab === 'fundamentals' && <StockFitFundamentals />}
          {tab === 'holders'      && <StockFit13F />}
        </div>
      </React.Suspense>
    </div>
  );
}
