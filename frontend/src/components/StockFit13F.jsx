import React, { useState, useEffect, useCallback } from 'react';
import { Building2, TrendingUp, TrendingDown, Plus, Minus, Search, Loader2, ArrowUpRight, Users, Sparkles } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api/stockfit/13f`;

const delegateToAI = (prompt) => {
  window.dispatchEvent(new CustomEvent('risedualai-open-chat', {
    detail: { prefill: prompt, autoSend: true },
  }));
};

const AskAIButton = ({ symbol, context, className = '' }) => {
  if (!symbol) return null;
  const prompt = context === 'holdings'
    ? `Walk me through ${symbol}: current price, recent performance, technical setup, and why major institutions hold it. Include any notable 13F moves this quarter.`
    : context?.startsWith('change-')
      ? `Why did ${context.split('|')[1]} ${context.split('|')[2]} their position in ${symbol} last quarter? Give me a concise take on the likely thesis and what it means for the stock.`
      : `Quick take on ${symbol}: institutional sentiment, technicals, and any recent news worth flagging.`;
  return (
    <button
      onClick={(e) => { e.stopPropagation(); delegateToAI(prompt); }}
      className={`inline-flex items-center justify-center w-5 h-5 rounded text-slate-500 hover:text-[#3DE8D9] hover:bg-[#3DE8D9]/10 transition-colors ${className}`}
      title={`Ask AI about ${symbol}`}
      data-testid={`stockfit-13f-ask-ai-${symbol}`}
    >
      <Sparkles className="w-3 h-3" />
    </button>
  );
};

const fmtUSD = (v) => {
  if (v == null) return 'N/A';
  const n = Number(v);
  if (Math.abs(n) >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString()}`;
};

const fmtShares = (v) => {
  if (v == null) return 'N/A';
  const n = Number(v);
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toLocaleString();
};

const TypeBadge = ({ type }) => {
  const map = {
    new: { cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', Icon: Plus },
    exited: { cls: 'bg-red-500/15 text-red-400 border-red-500/30', Icon: Minus },
    increased: { cls: 'bg-lime-500/15 text-lime-400 border-lime-500/30', Icon: TrendingUp },
    decreased: { cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30', Icon: TrendingDown },
  };
  const m = map[type] || { cls: 'bg-slate-600/30 text-slate-300', Icon: ArrowUpRight };
  const Icon = m.Icon;
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold border ${m.cls}`}>
      <Icon className="w-2.5 h-2.5" />
      {type?.toUpperCase()}
    </span>
  );
};

const HoldingsTable = ({ holdings, showInstitution = false }) => (
  <div className="overflow-x-auto" data-testid="stockfit-13f-holdings-table">
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-slate-700/50">
          {showInstitution && <th className="text-left text-slate-400 py-2 pr-3 font-medium">Institution</th>}
          <th className="text-left text-slate-400 py-2 pr-3 font-medium">Symbol</th>
          <th className="text-left text-slate-400 py-2 pr-3 font-medium">Issuer</th>
          <th className="text-right text-slate-400 py-2 px-2 font-medium">Shares</th>
          <th className="text-right text-slate-400 py-2 px-2 font-medium">Value</th>
          {showInstitution && <th className="text-left text-slate-400 py-2 pl-2 font-medium">As of</th>}
        </tr>
      </thead>
      <tbody>
        {holdings.map((h, i) => (
          <tr key={`${h.cik || ''}-${h.cusip || ''}-${i}`} className="border-b border-slate-800/40 hover:bg-slate-800/30">
            {showInstitution && (
              <td className="py-2 pr-3 text-slate-300 font-medium">{h.institution_name}</td>
            )}
            <td className="py-2 pr-3">
              {h.symbol ? (
                <span className="inline-flex items-center gap-1.5">
                  <span className="text-[#3DE8D9] font-bold">{h.symbol}</span>
                  <AskAIButton symbol={h.symbol} context="holdings" />
                </span>
              ) : (
                <span className="text-slate-500 text-[10px]">—</span>
              )}
            </td>
            <td className="py-2 pr-3 text-slate-300">{h.issuer}</td>
            <td className="py-2 px-2 text-right text-white tabular-nums">{fmtShares(h.shares)}</td>
            <td className="py-2 px-2 text-right text-white font-semibold tabular-nums">{fmtUSD(h.value_usd)}</td>
            {showInstitution && (
              <td className="py-2 pl-2 text-slate-500 text-[10px]">{h.period_end}</td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
    {holdings.length === 0 && (
      <p className="text-slate-500 text-xs text-center py-6">No holdings data available.</p>
    )}
  </div>
);

const ChangesTable = ({ changes, institutionName = '' }) => (
  <div className="overflow-x-auto" data-testid="stockfit-13f-changes-table">
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-slate-700/50">
          <th className="text-left text-slate-400 py-2 pr-3 font-medium">Change</th>
          <th className="text-left text-slate-400 py-2 pr-3 font-medium">Symbol</th>
          <th className="text-left text-slate-400 py-2 pr-3 font-medium">Issuer</th>
          <th className="text-right text-slate-400 py-2 px-2 font-medium">Δ Shares</th>
          <th className="text-right text-slate-400 py-2 px-2 font-medium">Δ %</th>
          <th className="text-right text-slate-400 py-2 pl-2 font-medium">Position Value</th>
        </tr>
      </thead>
      <tbody>
        {changes.map((c, i) => {
          const verbMap = { new: 'open a new position in', exited: 'exit its position in', increased: 'increase its stake in', decreased: 'trim its position in' };
          const verb = verbMap[c.type] || 'change its position in';
          const ctx = c.symbol ? `change-${c.type}|${institutionName || 'this institution'}|${verb}` : undefined;
          return (
            <tr key={`${c.cusip}-${c.type}-${i}`} className="border-b border-slate-800/40 hover:bg-slate-800/30">
              <td className="py-2 pr-3"><TypeBadge type={c.type} /></td>
              <td className="py-2 pr-3">
                {c.symbol ? (
                  <span className="inline-flex items-center gap-1.5">
                    <span className="text-[#3DE8D9] font-bold">{c.symbol}</span>
                    <AskAIButton symbol={c.symbol} context={ctx} />
                  </span>
                ) : (
                  <span className="text-slate-500 text-[10px]">—</span>
                )}
              </td>
              <td className="py-2 pr-3 text-slate-300">{c.issuer}</td>
              <td className={`py-2 px-2 text-right tabular-nums ${c.delta_shares > 0 ? 'text-emerald-400' : c.delta_shares < 0 ? 'text-red-400' : 'text-slate-500'}`}>
                {c.delta_shares > 0 ? '+' : ''}{fmtShares(c.delta_shares)}
              </td>
              <td className={`py-2 px-2 text-right tabular-nums ${c.delta_pct > 0 ? 'text-emerald-400' : c.delta_pct < 0 ? 'text-red-400' : 'text-slate-500'}`}>
                {c.delta_pct == null ? '—' : `${c.delta_pct > 0 ? '+' : ''}${Number(c.delta_pct).toFixed(1)}%`}
              </td>
              <td className="py-2 pl-2 text-right text-white font-semibold tabular-nums">{fmtUSD(c.value_usd)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
    {changes.length === 0 && (
      <p className="text-slate-500 text-xs text-center py-6">No QoQ changes — latest filing matches previous quarter.</p>
    )}
  </div>
);

export default function StockFit13F() {
  const [mode, setMode] = useState('symbol'); // 'symbol' | 'institution'
  const [symbolInput, setSymbolInput] = useState('');
  const [institutions, setInstitutions] = useState([]);
  const [selectedCik, setSelectedCik] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);
  const [changes, setChanges] = useState(null);

  // Load institutions list once
  useEffect(() => {
    fetch(`${API}/institutions`, { credentials: 'include' })
      .then(r => r.json())
      .then(d => setInstitutions(d.institutions || []))
      .catch(() => setInstitutions([]));
  }, []);

  const searchSymbol = useCallback(async () => {
    const sym = symbolInput.trim().toUpperCase();
    if (!sym) return;
    setLoading(true); setError(null); setData(null); setChanges(null);
    try {
      const r = await fetch(`${API}/holders/${sym}?limit=25`, { credentials: 'include' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Lookup failed');
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [symbolInput]);

  const loadInstitution = useCallback(async (cik) => {
    if (!cik) return;
    setSelectedCik(cik);
    setLoading(true); setError(null); setData(null); setChanges(null);
    try {
      const [holdRes, chgRes] = await Promise.all([
        fetch(`${API}/institution/${cik}?limit=25`, { credentials: 'include' }),
        fetch(`${API}/changes/${cik}?limit=15`, { credentials: 'include' }),
      ]);
      const h = await holdRes.json();
      if (!holdRes.ok) throw new Error(h.detail || 'Lookup failed');
      setData(h);
      if (chgRes.ok) {
        setChanges(await chgRes.json());
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Listen for shared ticker events from the Stock Detail hub.
  useEffect(() => {
    const handler = (e) => {
      const t = (e?.detail || '').toString().toUpperCase().trim();
      if (!t) return;
      setMode('symbol');
      setSymbolInput(t);
      // defer one tick so setSymbolInput lands before searchSymbol reads it
      setTimeout(() => {
        setLoading(true); setError(null); setData(null); setChanges(null);
        fetch(`${API}/holders/${t}?limit=25`, { credentials: 'include' })
          .then(r => r.json().then(d => ({ ok: r.ok, d })))
          .then(({ ok, d }) => {
            if (!ok) throw new Error(d.detail || 'Lookup failed');
            setData(d);
          })
          .catch(err => setError(err.message))
          .finally(() => setLoading(false));
      }, 0);
    };
    window.addEventListener('risedualai-research', handler);
    return () => window.removeEventListener('risedualai-research', handler);
  }, []);

  return (
    <div className="space-y-5" data-testid="stockfit-13f">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Building2 className="w-5 h-5 text-[#3DE8D9]" />
        <div>
          <h2 className="text-white font-bold text-base">13F Holder Tracking</h2>
          <p className="text-slate-400 text-xs">SEC EDGAR institutional filings — {institutions.length} top institutions tracked</p>
        </div>
      </div>

      {/* Mode toggle */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => { setMode('symbol'); setData(null); setChanges(null); setError(null); }}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${mode === 'symbol' ? 'bg-[#3DE8D9] text-slate-900' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
          data-testid="stockfit-13f-mode-symbol"
        >Lookup by Symbol</button>
        <button
          onClick={() => { setMode('institution'); setData(null); setChanges(null); setError(null); }}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${mode === 'institution' ? 'bg-[#3DE8D9] text-slate-900' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
          data-testid="stockfit-13f-mode-institution"
        >Browse by Institution</button>
      </div>

      {/* Input */}
      {mode === 'symbol' ? (
        <div className="flex gap-2">
          <Input
            placeholder="Enter ticker (e.g. AAPL, NVDA, MSFT)"
            value={symbolInput}
            onChange={e => setSymbolInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && searchSymbol()}
            className="bg-slate-800 border-slate-600 text-white h-9"
            data-testid="stockfit-13f-symbol-input"
          />
          <Button
            onClick={searchSymbol}
            disabled={loading || !symbolInput.trim()}
            className="bg-[#3DE8D9] hover:bg-[#2fd4c6] text-slate-900 h-9"
            data-testid="stockfit-13f-symbol-search"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          </Button>
        </div>
      ) : (
        <div className="flex gap-2">
          <select
            value={selectedCik}
            onChange={e => loadInstitution(e.target.value)}
            className="flex-1 bg-slate-800 border border-slate-600 text-white h-9 rounded-lg px-3 text-sm"
            data-testid="stockfit-13f-institution-select"
          >
            <option value="">Select an institution…</option>
            {institutions.map(i => (
              <option key={i.cik} value={i.cik}>
                {i.name} {i.latest_period_end ? `(${i.latest_period_end})` : '(not yet fetched)'}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Error */}
      {error && (
        <Card className="bg-red-900/20 border-red-700/40 p-3" data-testid="stockfit-13f-error">
          <p className="text-red-300 text-xs">{error}</p>
        </Card>
      )}

      {/* Symbol results */}
      {mode === 'symbol' && data && (
        <Card className="bg-slate-900/60 border-slate-700/40 p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-white font-medium text-sm flex items-center gap-2">
                <Users className="w-4 h-4 text-[#3DE8D9]" />
                Top Institutional Holders of {data.symbol}
                {data.company && <span className="text-slate-400 font-normal"> — {data.company}</span>}
              </h3>
              <p className="text-slate-500 text-[11px] mt-0.5">
                {data.holder_count || 0} institutions holding {fmtUSD(data.total_value_usd)} combined
              </p>
            </div>
          </div>
          <HoldingsTable holdings={data.holders || []} showInstitution={true} />
        </Card>
      )}

      {/* Institution results */}
      {mode === 'institution' && data?.filing && (
        <>
          <Card className="bg-slate-900/60 border-slate-700/40 p-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-white font-medium text-sm flex items-center gap-2">
                  <Building2 className="w-4 h-4 text-[#3DE8D9]" />
                  {data.institution_name} — Top Holdings
                </h3>
                <p className="text-slate-500 text-[11px] mt-0.5">
                  Filing period: {data.filing.period_end} · Filed: {data.filing.filing_date} · Total AUM: {fmtUSD(data.filing.total_value_usd)} · {data.filing.holdings_count} positions
                </p>
              </div>
              <Badge className="bg-[#3DE8D9]/15 text-[#3DE8D9] border-[#3DE8D9]/30 text-[10px]">13F-HR</Badge>
            </div>
            <HoldingsTable holdings={data.holdings || []} />
          </Card>

          {changes?.changes?.length > 0 && (
            <Card className="bg-slate-900/60 border-slate-700/40 p-4">
              <h3 className="text-white font-medium text-sm flex items-center gap-2 mb-3">
                <TrendingUp className="w-4 h-4 text-[#3DE8D9]" />
                Quarter-over-Quarter Changes
                <span className="text-slate-500 text-[11px] font-normal">
                  ({changes.previous?.period_end} → {changes.latest?.period_end} · {changes.total_changes} total changes)
                </span>
              </h3>
              <ChangesTable changes={changes.changes} institutionName={data.institution_name} />
            </Card>
          )}
        </>
      )}

      {/* Empty state */}
      {!data && !loading && !error && (
        <div className="text-center py-12">
          <Building2 className="w-10 h-10 text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400 text-sm">
            {mode === 'symbol' ? 'Enter a ticker to see which tracked institutions hold it' : 'Select an institution to view its top holdings and QoQ changes'}
          </p>
          <p className="text-slate-600 text-xs mt-1">
            Data sourced directly from SEC EDGAR 13F-HR filings · Updated daily
          </p>
        </div>
      )}

      <p className="text-slate-600 text-[10px] text-center pt-3 border-t border-slate-800/50">
        13F filings are filed by institutional investment managers with ≥$100M AUM, up to 45 days after quarter-end.
        Holdings reflect long positions only (common stock).
      </p>
    </div>
  );
}
