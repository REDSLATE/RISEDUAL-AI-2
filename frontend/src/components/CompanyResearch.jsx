import React, { useState, useEffect, useCallback } from 'react';
import { Search, Building2, TrendingUp, BarChart3, Globe, Users, Loader2, ExternalLink, ChevronDown, ChevronUp, Star } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { researchCompany } from '../services/api';

const formatMarketCap = (value) => {
  if (!value) return 'N/A';
  const num = parseFloat(value);
  if (isNaN(num)) return value;
  if (num >= 1e12) return `$${(num / 1e12).toFixed(2)}T`;
  if (num >= 1e9) return `$${(num / 1e9).toFixed(2)}B`;
  if (num >= 1e6) return `$${(num / 1e6).toFixed(1)}M`;
  return `$${num.toLocaleString()}`;
};

const formatRevenue = (value) => formatMarketCap(value);

const MetricCard = ({ label, value, prefix = '' }) => (
  <div className="bg-slate-700/60 rounded-lg p-3">
    <p className="text-slate-300 text-xs mb-1">{label}</p>
    <p className="text-white font-semibold text-sm">{prefix}{value || 'N/A'}</p>
  </div>
);

const SourceBadge = ({ source }) => (
  <a
    href={source.url || '#'}
    target="_blank"
    rel="noopener noreferrer"
    className="inline-flex items-center gap-1 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-xs px-2 py-1 rounded-full transition-colors"
    data-testid={`source-${source.number}`}
  >
    <span className="text-[#3DE8D9] font-bold">[{source.number}]</span>
    <span className="truncate max-w-[150px]">{source.source}</span>
    <ExternalLink className="w-3 h-3 shrink-0 opacity-50" />
  </a>
);

const ResearchCard = ({ data, compact = false }) => {
  const [expanded, setExpanded] = useState(!compact);
  const [inWatchlist, setInWatchlist] = useState(false);
  const overview = data.overview || {};

  useEffect(() => {
    const saved = localStorage.getItem('risedualai_watchlist');
    if (saved) {
      const list = JSON.parse(saved);
      setInWatchlist(list.some(item => item.symbol === data.symbol));
    }
  }, [data.symbol]);

  const addToWatchlist = () => {
    window.dispatchEvent(new CustomEvent('risedualai-add-watchlist', { detail: data.symbol }));
    setInWatchlist(true);
  };

  return (
    <Card className="bg-slate-700/55 border-slate-400/25 rounded-xl overflow-hidden" data-testid="research-card">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-400/25 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-[#3DE8D9] rounded-xl flex items-center justify-center">
            <Building2 className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-white font-bold text-lg" style={{fontFamily:'Manrope,sans-serif'}}>
                {data.company_name || data.symbol}
              </h3>
              <Badge className="bg-[#3DE8D9]/20 text-[#3DE8D9] border-0 text-xs">{data.symbol}</Badge>
            </div>
            <p className="text-slate-300 text-xs">
              {overview.sector || 'Technology'} &middot; {overview.exchange || 'NYSE'}
              {overview.employees && ` &middot; ${parseInt(overview.employees).toLocaleString()} employees`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={addToWatchlist}
            disabled={inWatchlist}
            className={inWatchlist ? "text-yellow-500" : "text-slate-400 hover:text-yellow-500"}
            data-testid="add-to-watchlist-btn"
          >
            <Star className={`w-4 h-4 ${inWatchlist ? 'fill-yellow-500' : ''}`} />
          </Button>
          {compact && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded(!expanded)}
              className="text-slate-400 hover:text-white"
              data-testid="expand-research"
            >
              {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </Button>
          )}
        </div>
      </div>

      {expanded && (
        <>
          {/* Key Metrics Grid */}
          {overview.market_cap && (
            <div className="px-5 py-3 border-b border-slate-400/25">
              <div className="grid grid-cols-4 gap-2">
                <MetricCard label="Market Cap" value={formatMarketCap(overview.market_cap)} />
                <MetricCard label="P/E Ratio" value={overview.pe_ratio} />
                <MetricCard label="EPS" value={overview.eps} prefix="$" />
                <MetricCard label="Revenue (TTM)" value={formatRevenue(overview.revenue_ttm)} />
              </div>
              <div className="grid grid-cols-4 gap-2 mt-2">
                <MetricCard label="52W High" value={overview.week_52_high} prefix="$" />
                <MetricCard label="52W Low" value={overview.week_52_low} prefix="$" />
                <MetricCard label="Div Yield" value={overview.dividend_yield ? `${(parseFloat(overview.dividend_yield) * 100).toFixed(2)}%` : 'N/A'} />
                <MetricCard label="Beta" value={overview.beta} />
              </div>
            </div>
          )}

          {/* AI Synthesis */}
          <div className="px-5 py-4">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-2 h-2 rounded-full bg-[#3DE8D9] animate-pulse" />
              <p className="text-xs font-bold text-[#3DE8D9] uppercase tracking-wider" style={{fontFamily:'Manrope,sans-serif'}}>
                AI Research Summary
              </p>
            </div>
            <div className="prose prose-sm prose-invert max-w-none text-slate-300 text-sm leading-relaxed research-content">
              {data.synthesis?.split('\n').map((line, i) => {
                const key = `line-${i}-${line.slice(0, 12)}`;
                if (line.startsWith('## ')) return <h3 key={key} className="text-white font-bold text-base mt-3 mb-2" style={{fontFamily:'Manrope,sans-serif'}}>{line.replace('## ', '')}</h3>;
                if (line.startsWith('**') && line.includes('**:')) {
                  const parts = line.split('**:');
                  const label = parts[0].replace(/\*\*/g, '');
                  const value = parts.slice(1).join('**:');
                  return <p key={key} className="mb-2"><strong className="text-white">{label}:</strong>{value}</p>;
                }
                if (line.startsWith('- ')) return <p key={key} className="ml-4 mb-1 text-slate-400">{line}</p>;
                if (line.trim() === '') return <br key={key} />;
                return <p key={key} className="mb-2">{line}</p>;
              })}
            </div>
          </div>

          {/* Sources */}
          {data.sources && data.sources.length > 0 && (
            <div className="px-5 py-3 border-t border-slate-400/25 bg-slate-700/50">
              <p className="text-xs text-slate-400 mb-2 font-medium">Sources ({data.sources.length})</p>
              <div className="flex flex-wrap gap-2">
                {data.sources.map((source) => (
                  <SourceBadge key={source.number} source={source} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </Card>
  );
};

// Standalone Research Section for the main page
const CompanyResearch = () => {
  const [symbol, setSymbol] = useState('');
  const [research, setResearch] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSearch = async (e) => {
    e?.preventDefault();
    if (!symbol.trim()) return;
    doSearch(symbol.trim().toUpperCase());
  };

  const doSearch = useCallback(async (ticker) => {
    setIsLoading(true);
    setError(null);
    setResearch(null);
    setSymbol(ticker);

    try {
      const data = await researchCompany(ticker);
      setResearch(data);
    } catch (err) {
      setError('Could not find data for this symbol. Please try another ticker.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const handleNavSearch = (e) => {
      if (e.detail) doSearch(e.detail);
    };
    window.addEventListener('risedualai-research', handleNavSearch);
    return () => window.removeEventListener('risedualai-research', handleNavSearch);
  }, [doSearch]);

  const popularTickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA'];

  return (
    <div className="space-y-6" data-testid="research-section">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-[#3DE8D9] rounded-xl flex items-center justify-center">
            <Globe className="w-5 h-5 text-white" />
          </div>
          <div>
            <h2 className="text-white text-lg sm:text-xl font-bold" style={{fontFamily:'Manrope,sans-serif'}}>Company Research</h2>
            <p className="text-slate-300 text-xs">Perplexity-style AI research with cited sources</p>
          </div>
        </div>
      </div>

      {/* Search Bar */}
      <form onSubmit={handleSearch} className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <Input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="Enter ticker symbol (e.g. AAPL, TSLA, MSFT)..."
            className="pl-10 bg-slate-700/60 border-slate-400/25 text-white placeholder-slate-500 rounded-xl"
            data-testid="research-input"
          />
        </div>
        <Button
          type="submit"
          disabled={isLoading || !symbol.trim()}
          className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl px-6"
          data-testid="research-search-btn"
        >
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Research'}
        </Button>
      </form>

      {/* Quick Tickers */}
      <div className="flex gap-2 flex-wrap">
        {popularTickers.map((ticker) => (
          <button
            key={ticker}
            onClick={() => { setSymbol(ticker); }}
            className="text-xs px-3 py-1.5 rounded-full border border-slate-400/25 text-slate-400 hover:text-[#3DE8D9] hover:border-[#3DE8D9] transition-colors"
            data-testid={`quick-ticker-${ticker}`}
          >
            {ticker}
          </button>
        ))}
      </div>

      {/* Loading State */}
      {isLoading && (
        <div className="flex flex-col items-center justify-center py-12 gap-3">
          <Loader2 className="w-8 h-8 text-[#3DE8D9] animate-spin" />
          <p className="text-slate-300 text-sm">Researching {symbol}... Gathering data from multiple sources</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-orange-400 text-sm">
          {error}
        </div>
      )}

      {/* Results */}
      {research && <ResearchCard data={research} />}
    </div>
  );
};

export { ResearchCard };
export default CompanyResearch;
