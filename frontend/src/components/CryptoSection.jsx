import React, { useState, useEffect, useCallback } from 'react';
import { Bitcoin, TrendingUp, TrendingDown, DollarSign } from 'lucide-react';
import { Card } from './ui/card';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

const CryptoSection = () => {
  const [cryptos, setCryptos] = useState([]);
  const [loading, setLoading] = useState(true);

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

  if (loading) {
    return (
      <div className="bg-[#0F1A2E] rounded-xl border border-slate-400/25 p-6">
        <div className="text-slate-400">Loading crypto data...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6 mt-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-orange-500 rounded-lg flex items-center justify-center">
          <Bitcoin className="w-6 h-6 text-white" />
        </div>
        <div>
          <h2 className="text-white text-lg sm:text-2xl font-bold">Cryptocurrency Market</h2>
          <p className="text-slate-300 text-sm">Real-time cryptocurrency prices and market data</p>
        </div>
      </div>

      {/* Crypto Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {cryptos.map((crypto) => (
          <Card key={crypto.symbol} className="bg-[#0F1A2E] border-slate-400/30 p-4 hover:border-slate-600 transition-colors">
            <div className="flex items-start justify-between mb-3">
              <div className="flex items-center gap-2">
                <div className="w-10 h-10 bg-gradient-to-br from-orange-500 to-orange-600 rounded-full flex items-center justify-center">
                  <span className="text-white font-bold text-sm">{crypto.symbol.substring(0, 2)}</span>
                </div>
                <div>
                  <h3 className="text-white font-semibold">{crypto.symbol}</h3>
                  <p className="text-slate-300 text-xs">{crypto.market}</p>
                </div>
              </div>
              {crypto.changePercent >= 0 ? (
                <TrendingUp className="w-5 h-5 text-lime-400" />
              ) : (
                <TrendingDown className="w-5 h-5 text-orange-400" />
              )}
            </div>
            
            <div className="space-y-2">
              <div>
                <p className="text-slate-300 text-xs mb-1">Price</p>
                <p className="text-white text-2xl font-bold">${crypto.price.toLocaleString()}</p>
              </div>
              
              <div className="flex items-center justify-between pt-2 border-t border-slate-400/30">
                <span className="text-slate-300 text-xs">24h Change</span>
                <span className={`text-sm font-medium ${
                  crypto.changePercent >= 0 ? 'text-lime-400' : 'text-orange-400'
                }`}>
                  {crypto.changePercent >= 0 ? '+' : ''}{crypto.changePercent.toFixed(2)}%
                </span>
              </div>
              
              {crypto.change !== 0 && (
                <div className="flex items-center justify-between">
                  <span className="text-slate-300 text-xs">24h Value</span>
                  <span className={`text-sm font-medium ${
                    crypto.change >= 0 ? 'text-lime-400' : 'text-orange-400'
                  }`}>
                    {crypto.change >= 0 ? '+' : ''}${Math.abs(crypto.change).toFixed(2)}
                  </span>
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>

      {/* Market Info */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-slate-700/60 border border-slate-400/25 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <DollarSign className="w-5 h-5 text-lime-400" />
            <h3 className="text-white font-semibold">Market Overview</h3>
          </div>
          <p className="text-slate-300 text-sm">
            Track the top cryptocurrencies with real-time price updates. Data refreshes every minute to keep you informed.
          </p>
        </div>
        
        <div className="bg-slate-700/60 border border-slate-400/25 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <Bitcoin className="w-5 h-5 text-orange-400" />
            <h3 className="text-white font-semibold">Trading Insights</h3>
          </div>
          <p className="text-slate-300 text-sm">
            Monitor price movements and percentage changes to identify trading opportunities in the volatile crypto market.
          </p>
        </div>
      </div>
    </div>
  );
};

export default CryptoSection;