import React, { useState, useEffect, useCallback } from 'react';
import { TrendingUp, TrendingDown } from 'lucide-react';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();
const API = `${BACKEND_URL}/api`;

const COIN_COLORS = {
  BT: '#F7931A', ET: '#627EEA', BN: '#F3BA2F', SO: '#9945FF', XR: '#23292F',
  AD: '#0033AD', DO: '#C2A633', AV: '#E84142', PO: '#E6007A', MA: '#8247E5',
  LI: '#2A5ADA', SH: '#FF5CAA', LT: '#345D9D', UN: '#FF007A', AT: '#2E3148',
};

const getHeatColor = (pct) => {
  // Mirror SectorTile heat scale. Lower thresholds because crypto moves harder.
  if (pct >= 5) return 'bg-green-500 text-white';
  if (pct >= 2.5) return 'bg-lime-500 text-white';
  if (pct >= 1) return 'bg-lime-400 text-gray-900';
  if (pct >= 0) return 'bg-yellow-400 text-gray-900';
  if (pct >= -1) return 'bg-yellow-500 text-gray-900';
  if (pct >= -2.5) return 'bg-orange-500 text-white';
  if (pct >= -5) return 'bg-red-500 text-white';
  return 'bg-red-600 text-white';
};

const dispatchDeepLink = (symbol) => {
  try {
    fetch(`${API}/analytics/chip-event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        action: 'action-clicked',
        chip_text: `Open ${symbol} War Room (Crypto Heatmap)`,
        context_hub: typeof window !== 'undefined' ? (window.__risedualActiveView || null) : null,
      }),
    }).catch(() => { /* silent */ });
  } catch { /* silent */ }
  window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: 'warroom', subTab: 'adversarial' } }));
  setTimeout(() => {
    window.dispatchEvent(new CustomEvent('risedualai-warroom', { detail: symbol }));
  }, 180);
};

const CryptoTile = ({ crypto }) => {
  const abbr = crypto.symbol.substring(0, 2);
  const coinBg = COIN_COLORS[abbr] || '#6366f1';
  const pct = crypto.changePercent || 0;
  const up = pct >= 0;
  const heat = getHeatColor(pct);

  return (
    <button
      type="button"
      onClick={() => dispatchDeepLink(crypto.symbol)}
      className={`${heat} text-left w-full rounded-xl p-3 transition-all hover:scale-[1.02] hover:brightness-110 cursor-pointer relative overflow-hidden`}
      style={{ minHeight: '84px' }}
      data-testid={`crypto-tile-${crypto.symbol}`}
      title={`Click to open ${crypto.symbol} in War Room`}
    >
      <div className="absolute top-0 right-0 opacity-10 text-5xl font-black leading-none select-none pointer-events-none" style={{ marginTop: '-4px', marginRight: '-2px' }}>
        {crypto.symbol}
      </div>
      <div className="relative z-10">
        <div className="flex items-center gap-1.5 mb-1">
          <div
            className="w-5 h-5 rounded-full flex items-center justify-center shrink-0"
            style={{ backgroundColor: coinBg }}
          >
            <span className="text-white text-[8px] font-bold">{abbr}</span>
          </div>
          <span className="font-bold text-sm">{crypto.symbol}</span>
          <span className="text-[10px] opacity-70">/USD</span>
        </div>
        <div className="flex items-end justify-between">
          <span className="text-base font-black tabular-nums">
            ${crypto.price >= 1000 ? crypto.price.toLocaleString(undefined, { maximumFractionDigits: 0 }) : crypto.price?.toFixed(2)}
          </span>
          <span className="text-xs font-bold tabular-nums inline-flex items-center gap-0.5">
            {up ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
            {up ? '+' : ''}{pct.toFixed(2)}%
          </span>
        </div>
      </div>
    </button>
  );
};

const CryptoTicker = () => {
  const [cryptos, setCryptos] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchCryptoData = useCallback(async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/crypto/prices`);
      if (response.ok) {
        const data = await response.json();
        setCryptos(Array.isArray(data) ? data : []);
      }
    } catch (error) {
      logger.error('Error fetching crypto data:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCryptoData();
    const interval = setInterval(fetchCryptoData, 60000);
    return () => clearInterval(interval);
  }, [fetchCryptoData]);

  if (loading && cryptos.length === 0) {
    return <div className="text-slate-400 text-sm py-4">Loading crypto data...</div>;
  }
  if (!cryptos.length) return null;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2" data-testid="crypto-heatmap">
      {cryptos.slice(0, 8).map((c) => (
        <CryptoTile key={c.symbol} crypto={c} />
      ))}
    </div>
  );
};

export default CryptoTicker;
