import React, { useState, useEffect, useCallback } from 'react';
import { TrendingUp, TrendingDown, DollarSign, Percent, ArrowRight, Info } from 'lucide-react';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';

const API = `${getApiBase()}/api`;

const SuccessFeeWidget = () => {
  const [fee, setFee] = useState(null);
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchFee = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/success-fee/current`);
      if (res.ok) {
        const data = await res.json();
        setFee(data);
      }
    } catch (e) {
      logger.warn('Success fee fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/success-fee/history?limit=6`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.records || []);
      }
    } catch (e) {
      logger.warn('Fee history fetch error:', e);
    }
  }, []);

  useEffect(() => { fetchFee(); }, [fetchFee]);

  // Not eligible (no broker connections) — don't render
  if (!loading && (!fee || !fee.eligible)) return null;

  if (loading) {
    return (
      <div className="bg-slate-700/40 rounded-xl border border-slate-500/20 p-4 animate-pulse" data-testid="success-fee-widget-loading">
        <div className="h-4 bg-slate-600/50 rounded w-1/3 mb-3" />
        <div className="h-8 bg-slate-600/50 rounded w-1/2" />
      </div>
    );
  }

  const profit = fee.profit || 0;
  const isPositive = profit > 0;
  const feeAmount = fee.fee_amount || 0;
  const threshold = fee.threshold || 1000;
  const thresholdProgress = isPositive ? Math.min((profit / threshold) * 100, 100) : 0;
  const aboveThreshold = profit > threshold;

  const statusBadge = {
    no_fee: { label: 'No Fee', color: 'text-slate-400 bg-slate-700/60' },
    pending: { label: 'Pending', color: 'text-slate-400 bg-slate-700/60' },
    unpaid: { label: 'Due', color: 'text-amber-400 bg-amber-500/15' },
    paid: { label: 'Paid', color: 'text-lime-400 bg-lime-500/15' },
    waived: { label: 'Waived', color: 'text-violet-400 bg-violet-500/15' },
  }[fee.status] || { label: fee.status, color: 'text-slate-400 bg-slate-700/60' };

  return (
    <div className="bg-slate-700/40 rounded-xl border border-slate-500/20 overflow-hidden" data-testid="success-fee-widget">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-600/30 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-[#3DE8D9]/10 flex items-center justify-center">
            <Percent className="w-4 h-4 text-[#3DE8D9]" />
          </div>
          <div>
            <h3 className="text-white text-sm font-bold">Success Fee</h3>
            <span className="text-slate-400 text-[10px]">{fee.period} &middot; 1.5% on gains above ${threshold.toLocaleString()}</span>
          </div>
        </div>
        <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full ${statusBadge.color}`} data-testid="fee-status-badge">
          {statusBadge.label}
        </span>
      </div>

      {/* Body */}
      <div className="p-4">
        {/* Profit / Loss display */}
        <div className="flex items-center gap-3 mb-4">
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${isPositive ? 'bg-lime-500/10' : 'bg-red-500/10'}`}>
            {isPositive ? <TrendingUp className="w-5 h-5 text-lime-400" /> : <TrendingDown className="w-5 h-5 text-red-400" />}
          </div>
          <div>
            <p className="text-slate-400 text-[10px]">Monthly P&L</p>
            <p className={`text-xl font-bold ${isPositive ? 'text-lime-400' : 'text-red-400'}`} data-testid="fee-profit-amount">
              {isPositive ? '+' : ''}{profit < 0 ? '-' : ''}${Math.abs(profit).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </p>
          </div>
          {feeAmount > 0 && (
            <div className="ml-auto text-right">
              <p className="text-slate-400 text-[10px]">Fee Owed</p>
              <p className="text-amber-400 text-lg font-bold" data-testid="fee-amount">${feeAmount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
            </div>
          )}
        </div>

        {/* Threshold progress */}
        {isPositive && (
          <div className="mb-4">
            <div className="flex items-center justify-between text-[10px] mb-1">
              <span className="text-slate-400">Threshold Progress</span>
              <span className={aboveThreshold ? 'text-amber-400 font-semibold' : 'text-slate-400'}>
                ${profit.toLocaleString(undefined, { maximumFractionDigits: 0 })} / ${threshold.toLocaleString()}
              </span>
            </div>
            <div className="h-1.5 bg-slate-800/80 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-700 ${aboveThreshold ? 'bg-amber-400' : 'bg-[#3DE8D9]'}`}
                style={{ width: `${thresholdProgress}%` }}
                data-testid="fee-threshold-bar"
              />
            </div>
            {!aboveThreshold && (
              <p className="text-slate-500 text-[9px] mt-1">
                ${(threshold - profit).toLocaleString(undefined, { maximumFractionDigits: 0 })} more in gains before fee applies
              </p>
            )}
          </div>
        )}

        {/* No fee on losses message */}
        {!isPositive && (
          <div className="flex items-start gap-2 bg-slate-800/50 rounded-lg p-2.5 mb-4">
            <Info className="w-3.5 h-3.5 text-slate-400 mt-0.5 shrink-0" />
            <p className="text-slate-400 text-[10px] leading-relaxed">
              No success fee during downturns. Fees only apply on realized gains above ${threshold.toLocaleString()}.
            </p>
          </div>
        )}

        {/* Balance row */}
        <div className="grid grid-cols-2 gap-3 mb-3">
          <div className="bg-slate-800/50 rounded-lg p-2.5">
            <p className="text-slate-400 text-[9px]">Start of Month</p>
            <p className="text-white text-xs font-semibold">${(fee.starting_balance || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</p>
          </div>
          <div className="bg-slate-800/50 rounded-lg p-2.5">
            <p className="text-slate-400 text-[9px]">Current Value</p>
            <p className="text-white text-xs font-semibold">${(fee.current_balance || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</p>
          </div>
        </div>

        {/* History toggle */}
        <button
          onClick={() => { setShowHistory(!showHistory); if (!showHistory && history.length === 0) fetchHistory(); }}
          className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-[#3DE8D9] transition-colors"
          data-testid="fee-history-toggle"
        >
          <ArrowRight className={`w-3 h-3 transition-transform ${showHistory ? 'rotate-90' : ''}`} />
          Fee History
        </button>

        {/* History rows */}
        {showHistory && (
          <div className="mt-2 space-y-1" data-testid="fee-history-list">
            {history.length === 0 ? (
              <p className="text-slate-500 text-[10px] py-2">No previous fee records</p>
            ) : (
              history.map(r => (
                <div key={r.period} className="flex items-center justify-between bg-slate-800/40 rounded-lg px-3 py-2">
                  <div>
                    <span className="text-white text-[10px] font-medium">{r.period}</span>
                    <span className={`ml-2 text-[10px] ${r.profit > 0 ? 'text-lime-400' : 'text-red-400'}`}>
                      {r.profit > 0 ? '+' : ''}{r.profit < 0 ? '-' : ''}${Math.abs(r.profit || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-amber-400 text-[10px] font-semibold">
                      {(r.fee_amount || 0) > 0 ? `$${r.fee_amount.toFixed(2)}` : '--'}
                    </span>
                    <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-full ${
                      r.status === 'paid' ? 'text-lime-400 bg-lime-500/15' :
                      r.status === 'waived' ? 'text-violet-400 bg-violet-500/15' :
                      r.status === 'unpaid' ? 'text-amber-400 bg-amber-500/15' :
                      'text-slate-400 bg-slate-700/60'
                    }`}>
                      {r.status === 'no_fee' ? '--' : r.status}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default SuccessFeeWidget;
