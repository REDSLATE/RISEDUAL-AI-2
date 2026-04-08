import React, { useEffect, useRef, useState, useCallback } from 'react';
import { getTickerData } from '../services/api';
import logger from '../utils/logger';

const StockTicker = () => {
  const tickerRef = useRef(null);
  const [stockData, setStockData] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchTickerData = useCallback(async () => {
    try {
      const data = await getTickerData();
      if (data && data.length > 0) {
        setStockData(data);
      }
    } catch (error) {
      logger.error('Failed to fetch ticker data:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTickerData();
    const interval = setInterval(fetchTickerData, 30000);
    return () => clearInterval(interval);
  }, [fetchTickerData]);

  useEffect(() => {
    if (stockData.length === 0) return;

    const ticker = tickerRef.current;
    if (!ticker) return;

    let animationId;
    let position = 0;

    const animate = () => {
      position -= 1;
      if (position <= -ticker.scrollWidth / 2) {
        position = 0;
      }
      ticker.style.transform = `translateX(${position}px)`;
      animationId = requestAnimationFrame(animate);
    };

    animationId = requestAnimationFrame(animate);

    return () => cancelAnimationFrame(animationId);
  }, [stockData]);

  const TickerItem = ({ symbol, price, change, changePercent }) => {
    const isNegative = change < 0;
    return (
      <div className="inline-flex items-center gap-2 px-4 whitespace-nowrap">
        <span className="text-white font-medium">{symbol}</span>
        <span className="text-slate-300 text-sm">{price.toFixed(2)}</span>
        <span className={`text-sm ${isNegative ? 'text-red-400' : 'text-emerald-400'}`}>
          {change.toFixed(2)} ({changePercent.toFixed(2)}%)
        </span>
      </div>
    );
  };

  if (loading) {
    return (
      <div className="bg-[#0F172A] border-b border-slate-700 overflow-hidden">
        <div className="flex py-2 px-6">
          <span className="text-slate-400 text-sm">Loading market data...</span>
        </div>
      </div>
    );
  }

  // Duplicate data for seamless loop
  const duplicatedData = [...stockData, ...stockData, ...stockData];

  return (
    <div className="bg-[#0F172A] border-b border-slate-700 overflow-hidden">
      <div ref={tickerRef} className="flex py-2">
        {duplicatedData.map((stock, index) => (
          <TickerItem key={`${stock.symbol}-${index}`} {...stock} />
        ))}
      </div>
    </div>
  );
};

export default StockTicker;
