import React, { useEffect, useRef } from 'react';
import { stockTickerData } from '../mockData';

const StockTicker = () => {
  const tickerRef = useRef(null);

  useEffect(() => {
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
  }, []);

  const TickerItem = ({ symbol, price, change, changePercent }) => {
    const isNegative = change < 0;
    return (
      <div className="inline-flex items-center gap-2 px-4 whitespace-nowrap">
        <span className="text-white font-medium">{symbol}</span>
        <span className="text-gray-300 text-sm">{price.toFixed(2)}</span>
        <span className={`text-sm ${isNegative ? 'text-red-500' : 'text-green-500'}`}>
          {change.toFixed(2)} ({changePercent.toFixed(2)}%)
        </span>
      </div>
    );
  };

  // Duplicate data for seamless loop
  const duplicatedData = [...stockTickerData, ...stockTickerData, ...stockTickerData];

  return (
    <div className="bg-[#0a0a0b] border-b border-gray-800 overflow-hidden">
      <div ref={tickerRef} className="flex py-2">
        {duplicatedData.map((stock, index) => (
          <TickerItem key={index} {...stock} />
        ))}
      </div>
    </div>
  );
};

export default StockTicker;