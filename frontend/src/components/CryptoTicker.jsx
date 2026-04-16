import React, { useState, useEffect, useCallback, useRef } from 'react';
import { TrendingUp, TrendingDown } from 'lucide-react';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

const COIN_COLORS = {
  BT: '#F7931A', ET: '#627EEA', BN: '#F3BA2F', SO: '#9945FF', XR: '#23292F',
  AD: '#0033AD', DO: '#C2A633', AV: '#E84142', PO: '#E6007A', MA: '#8247E5',
  LI: '#2A5ADA', SH: '#FF5CAA', LT: '#345D9D', UN: '#FF007A', AT: '#2E3148',
};

const CryptoTicker = () => {
  const [cryptos, setCryptos] = useState([]);
  const [loading, setLoading] = useState(true);
  const tickerRef = useRef(null);

  const fetchCryptoData = useCallback(async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/crypto/prices`);
      if (response.ok) {
        const data = await response.json();
        setCryptos(data);
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

  useEffect(() => {
    if (cryptos.length === 0) return;
    const ticker = tickerRef.current;
    if (!ticker) return;

    let animationId;
    let position = 0;

    const animate = () => {
      position -= 0.6;
      if (position <= -ticker.scrollWidth / 3) {
        position = 0;
      }
      ticker.style.transform = `translateX(${position}px)`;
      animationId = requestAnimationFrame(animate);
    };

    animationId = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animationId);
  }, [cryptos]);

  if (loading) {
    return (
      <div className="bg-[#060E1F] border-b border-slate-400/30 overflow-hidden">
        <div className="flex py-3 px-6">
          <span className="text-slate-300 text-sm">Loading crypto data...</span>
        </div>
      </div>
    );
  }

  const tripled = [...cryptos, ...cryptos, ...cryptos];

  return (
    <div className="bg-[#060E1F] border-b border-slate-400/30 overflow-hidden">
      <div ref={tickerRef} className="flex py-2.5" style={{ willChange: 'transform' }}>
        {tripled.map((crypto, i) => {
          const abbr = crypto.symbol.substring(0, 2);
          const bg = COIN_COLORS[abbr] || '#6366f1';
          const up = crypto.changePercent >= 0;
          return (
            <div key={`${crypto.symbol}-${i}`} className="inline-flex items-center gap-2.5 px-5 whitespace-nowrap">
              <div
                className="w-7 h-7 rounded-full flex items-center justify-center shrink-0"
                style={{ backgroundColor: bg }}
              >
                <span className="text-white text-[10px] font-bold">{abbr}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-white font-medium text-sm">{crypto.symbol}</span>
                <span className="text-slate-500 text-xs">/USD</span>
              </div>
              <span className="text-white text-sm font-medium">${crypto.price?.toLocaleString()}</span>
              <span className={`text-xs flex items-center gap-0.5 ${up ? 'text-lime-400' : 'text-orange-400'}`}>
                {up ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                {Math.abs(crypto.changePercent || 0).toFixed(2)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default CryptoTicker;
