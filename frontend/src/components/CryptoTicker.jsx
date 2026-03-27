import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Activity } from 'lucide-react';
import { Card } from './ui/card';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const CryptoTicker = () => {
  const [cryptos, setCryptos] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchCryptoData();
    const interval = setInterval(fetchCryptoData, 60000); // Update every minute
    return () => clearInterval(interval);
  }, []);

  const fetchCryptoData = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/crypto/prices`);
      if (response.ok) {
        const data = await response.json();
        setCryptos(data);
      }
    } catch (error) {
      console.error('Error fetching crypto data:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="bg-[#0F172A] border-b border-slate-700 py-4 px-6">
        <div className="text-slate-400 text-sm">Loading crypto data...</div>
      </div>
    );
  }

  return (
    <div className="bg-[#0F172A] border-b border-slate-700">
      <div className="overflow-x-auto">
        <div className="flex gap-6 px-6 py-3 min-w-max">
          {cryptos.map((crypto) => (
            <div key={crypto.symbol} className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 bg-orange-500 rounded-full flex items-center justify-center">
                  <span className="text-white text-xs font-bold">{crypto.symbol.substring(0, 2)}</span>
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-white font-medium text-sm">{crypto.symbol}</span>
                    <span className="text-slate-400 text-xs">/USD</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-white text-sm">${crypto.price.toLocaleString()}</span>
                    <span className={`text-xs flex items-center gap-1 ${
                      crypto.changePercent >= 0 ? 'text-emerald-400' : 'text-red-400'
                    }`}>
                      {crypto.changePercent >= 0 ? (
                        <TrendingUp className="w-3 h-3" />
                      ) : (
                        <TrendingDown className="w-3 h-3" />
                      )}
                      {Math.abs(crypto.changePercent).toFixed(2)}%
                    </span>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default CryptoTicker;