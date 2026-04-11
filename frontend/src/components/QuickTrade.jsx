import React, { useState } from 'react';
import { TrendingUp, TrendingDown, DollarSign, AlertCircle } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Label } from './ui/label';
import axios from 'axios';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

const QuickTrade = ({ symbol, currentPrice }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [orderData, setOrderData] = useState({
    symbol: symbol || '',
    side: 'buy',
    quantity: 1,
    type: 'market',
    limitPrice: '',
    broker: 'alpaca'
  });
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState(null);

  const handleTrade = async () => {
    setIsLoading(true);
    setResult(null);

    try {
      const order = {
        symbol: orderData.symbol.toUpperCase(),
        quantity: parseInt(orderData.quantity),
        side: orderData.side,
        type: orderData.type,
        time_in_force: 'day'
      };

      if (orderData.type === 'limit' && orderData.limitPrice) {
        order.limit_price = parseFloat(orderData.limitPrice);
      }

      const response = await axios.post(
        `${BACKEND_URL}/api/trading/order/${orderData.broker}`,
        order
      );

      setResult({
        success: true,
        message: 'Order placed successfully!',
        orderId: response.data.id
      });

      // Reset form after successful trade
      setTimeout(() => {
        setIsOpen(false);
        setResult(null);
      }, 3000);
    } catch (error) {
      setResult({
        success: false,
        message: error.response?.data?.detail || 'Failed to place order'
      });
    } finally {
      setIsLoading(false);
    }
  };

  const estimatedCost = orderData.quantity * (orderData.type === 'limit' ? parseFloat(orderData.limitPrice || 0) : (currentPrice || 0));

  return (
    <>
      {/* Quick Trade Button */}
      <div className="flex gap-2">
        <Button
          onClick={() => {
            setOrderData({ ...orderData, side: 'buy', symbol: symbol || '' });
            setIsOpen(true);
          }}
          className="bg-green-600 hover:bg-green-700 flex items-center gap-2"
          size="sm"
        >
          <TrendingUp className="w-4 h-4" />
          Buy
        </Button>
        <Button
          onClick={() => {
            setOrderData({ ...orderData, side: 'sell', symbol: symbol || '' });
            setIsOpen(true);
          }}
          className="bg-red-600 hover:bg-red-700 flex items-center gap-2"
          size="sm"
        >
          <TrendingDown className="w-4 h-4" />
          Sell
        </Button>
      </div>

      {/* Trading Modal */}
      {isOpen && (
        <div className="fixed inset-0 bg-black bg-opacity-70 z-50 flex items-center justify-center p-4">
          <Card className="bg-slate-800/50 border-slate-700/50 rounded-xl p-6 w-full max-w-md">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-white text-xl font-bold">
                {orderData.side === 'buy' ? 'Buy' : 'Sell'} {orderData.symbol}
              </h3>
              <button
                onClick={() => setIsOpen(false)}
                className="text-slate-400 hover:text-slate-50 text-2xl"
              >
                ×
              </button>
            </div>

            {/* Current Price */}
            {currentPrice && (
              <div className="bg-[#1E293B] rounded-lg p-3 mb-4">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400 text-sm">Current Price</span>
                  <span className="text-white font-semibold">${currentPrice.toFixed(2)}</span>
                </div>
              </div>
            )}

            {/* Order Form */}
            <div className="space-y-4">
              {/* Symbol */}
              <div>
                <Label className="text-white mb-2 block">Symbol</Label>
                <Input
                  value={orderData.symbol}
                  onChange={(e) => setOrderData({ ...orderData, symbol: e.target.value })}
                  placeholder="AAPL"
                  className="bg-[#1E293B] border-slate-600 text-white"
                />
              </div>

              {/* Order Type */}
              <div>
                <Label className="text-white mb-2 block">Order Type</Label>
                <Select
                  value={orderData.type}
                  onValueChange={(value) => setOrderData({ ...orderData, type: value })}
                >
                  <SelectTrigger className="bg-[#1E293B] border-slate-600 text-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#0A2A63] border-slate-600 shadow-2xl shadow-black/60">
                    <SelectItem value="market" className="text-white">Market Order</SelectItem>
                    <SelectItem value="limit" className="text-white">Limit Order</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Limit Price (if limit order) */}
              {orderData.type === 'limit' && (
                <div>
                  <Label className="text-white mb-2 block">Limit Price</Label>
                  <Input
                    type="number"
                    step="0.01"
                    value={orderData.limitPrice}
                    onChange={(e) => setOrderData({ ...orderData, limitPrice: e.target.value })}
                    placeholder="0.00"
                    className="bg-[#1E293B] border-slate-600 text-white"
                  />
                </div>
              )}

              {/* Quantity */}
              <div>
                <Label className="text-white mb-2 block">Quantity</Label>
                <Input
                  type="number"
                  min="1"
                  value={orderData.quantity}
                  onChange={(e) => setOrderData({ ...orderData, quantity: e.target.value })}
                  className="bg-[#1E293B] border-slate-600 text-white"
                />
              </div>

              {/* Broker */}
              <div>
                <Label className="text-white mb-2 block">Broker</Label>
                <Select
                  value={orderData.broker}
                  onValueChange={(value) => setOrderData({ ...orderData, broker: value })}
                >
                  <SelectTrigger className="bg-[#1E293B] border-slate-600 text-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#0A2A63] border-slate-600 shadow-2xl shadow-black/60">
                    <SelectItem value="alpaca" className="text-white">Alpaca</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Estimated Cost */}
              <div className="bg-[#1E293B] rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Estimated {orderData.side === 'buy' ? 'Cost' : 'Value'}</span>
                  <div className="flex items-center gap-1">
                    <DollarSign className="w-4 h-4 text-emerald-400" />
                    <span className="text-white font-semibold">{estimatedCost.toFixed(2)}</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Result Message */}
            {result && (
              <div className={`mt-4 p-3 rounded-lg flex items-start gap-2 ${
                result.success ? 'bg-emerald-900 bg-opacity-20 border border-green-800' : 'bg-red-900 bg-opacity-20 border border-red-800'
              }`}>
                <AlertCircle className={`w-5 h-5 flex-shrink-0 ${result.success ? 'text-emerald-400' : 'text-red-400'}`} />
                <div>
                  <p className={result.success ? 'text-emerald-400' : 'text-red-400'}>{result.message}</p>
                  {result.orderId && (
                    <p className="text-slate-400 text-xs mt-1">Order ID: {result.orderId}</p>
                  )}
                </div>
              </div>
            )}

            {/* Action Buttons */}
            <div className="flex gap-3 mt-6">
              <Button
                onClick={() => setIsOpen(false)}
                variant="outline"
                className="flex-1 bg-[#1E293B] border-slate-600 text-white hover:bg-slate-700"
              >
                Cancel
              </Button>
              <Button
                onClick={handleTrade}
                disabled={isLoading || !orderData.symbol || !orderData.quantity}
                className={`flex-1 ${
                  orderData.side === 'buy' 
                    ? 'bg-green-600 hover:bg-green-700' 
                    : 'bg-red-600 hover:bg-red-700'
                }`}
              >
                {isLoading ? 'Placing Order...' : `${orderData.side === 'buy' ? 'Buy' : 'Sell'} ${orderData.quantity} ${orderData.symbol}`}
              </Button>
            </div>

            {/* Warning */}
            <p className="text-slate-500 text-xs mt-4 text-center">
              ⚠️ Trading involves risk. This is connected to your real brokerage account.
            </p>
          </Card>
        </div>
      )}
    </>
  );
};

export default QuickTrade;
