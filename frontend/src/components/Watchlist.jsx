import React, { useState, useEffect } from 'react';
import { Star, X, Plus, TrendingUp, TrendingDown } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';

const Watchlist = () => {
  const [watchlist, setWatchlist] = useState([]);
  const [newSymbol, setNewSymbol] = useState('');
  const [isExpanded, setIsExpanded] = useState(false);

  useEffect(() => {
    // Load watchlist from localStorage
    const saved = localStorage.getItem('risedualai_watchlist');
    if (saved) {
      setWatchlist(JSON.parse(saved));
    }
  }, []);

  const addSymbol = () => {
    if (!newSymbol.trim()) return;
    
    const symbol = newSymbol.toUpperCase().trim();
    if (watchlist.find(item => item.symbol === symbol)) {
      alert('Symbol already in watchlist');
      return;
    }

    const newItem = {
      symbol,
      addedAt: new Date().toISOString(),
      price: 0,
      change: 0,
      changePercent: 0
    };

    const updated = [...watchlist, newItem];
    setWatchlist(updated);
    localStorage.setItem('risedualai_watchlist', JSON.stringify(updated));
    setNewSymbol('');
  };

  const removeSymbol = (symbol) => {
    const updated = watchlist.filter(item => item.symbol !== symbol);
    setWatchlist(updated);
    localStorage.setItem('risedualai_watchlist', JSON.stringify(updated));
  };

  return (
    <Card className="bg-[#1a1a1b] border-gray-800 p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Star className="w-5 h-5 text-yellow-500 fill-yellow-500" />
          <h3 className="text-white font-semibold">My Watchlist</h3>
          <span className="text-gray-500 text-sm">({watchlist.length})</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setIsExpanded(!isExpanded)}
          className="text-gray-400 hover:text-white"
        >
          {isExpanded ? 'Collapse' : 'Expand'}
        </Button>
      </div>

      {isExpanded && (
        <>
          {/* Add Symbol Input */}
          <div className="flex gap-2 mb-4">
            <Input
              placeholder="Add symbol (e.g., AAPL)"
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && addSymbol()}
              className="bg-[#272729] border-gray-700 text-white"
            />
            <Button onClick={addSymbol} className="bg-blue-600 hover:bg-blue-700">
              <Plus className="w-4 h-4" />
            </Button>
          </div>

          {/* Watchlist Items */}
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {watchlist.length === 0 ? (
              <div className="text-center py-8 text-gray-500">
                <Star className="w-12 h-12 mx-auto mb-2 opacity-20" />
                <p>Your watchlist is empty</p>
                <p className="text-sm">Add symbols to track them</p>
              </div>
            ) : (
              watchlist.map((item) => (
                <div
                  key={item.symbol}
                  className="flex items-center justify-between p-3 bg-[#272729] rounded-lg hover:bg-[#2a2a2c] transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-white font-medium">{item.symbol}</span>
                    {item.changePercent !== 0 && (
                      <div className={`flex items-center gap-1 text-sm ${
                        item.changePercent >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {item.changePercent >= 0 ? (
                          <TrendingUp className="w-3 h-3" />
                        ) : (
                          <TrendingDown className="w-3 h-3" />
                        )}
                        {Math.abs(item.changePercent).toFixed(2)}%
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {item.price > 0 && (
                      <span className="text-gray-300 text-sm">${item.price.toFixed(2)}</span>
                    )}
                    <button
                      onClick={() => removeSymbol(item.symbol)}
                      className="text-gray-500 hover:text-red-400 transition-colors"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </Card>
  );
};

export default Watchlist;