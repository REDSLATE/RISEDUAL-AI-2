/**
 * OptionsPaperTrade — paper-only Buy/Sell on Options scanner rows.
 *
 * Replaces the old equity `QuickTrade` on options tables. Always
 * posts to `/api/paper/trade` with the nested `option` payload; the
 * backend routes to `paper_options_service` and simulates a Black-
 * Scholes fill.
 *
 * UI intent:
 *   • Big yellow PAPER badge on the buttons — no chance a user
 *     confuses this with real-money execution while the multi-broker
 *     live-options flow is still deferred.
 *   • Confirms strike/expiry/side in a modal before firing.
 *   • Expiry defaults to the next monthly-expiration Friday (third
 *     Friday of the next month, standard OCC convention).
 *
 * Props:
 *   row — scanner row, expected shape:
 *     `{ contract: "CORZ", price: "$16 Put", ivRank: 30, ... }`
 *   defaultSide — "buy" or "sell". The scanner's green/red buttons
 *     pass this so each row shows both actions.
 */
import React, { useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, AlertCircle, Info } from 'lucide-react';
import axios from 'axios';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { Label } from './ui/label';
import { TradeModePill } from './ui/TradeModePill';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';
import TradingModeBanner from './TradingModeBanner';

const API = `${getApiBase()}/api`;

/** Parse a scanner row's `price` column (e.g. `"$16 Put"`) into
 *  strike + option type. Returns `null` when the string isn't in
 *  that shape so the caller can hide the buttons rather than render
 *  broken UI. */
function parseContract(priceField) {
  if (!priceField || typeof priceField !== 'string') return null;
  const m = priceField.match(/\$([\d.]+)\s+(Call|Put)/i);
  if (!m) return null;
  return { strike: parseFloat(m[1]), type: m[2].toLowerCase() };
}

/** Third-Friday-of-next-month, ISO date string. That's the standard
 *  monthly equity-options expiration. Used as the default expiry for
 *  any row that doesn't carry its own. */
function defaultMonthlyExpiry() {
  const now = new Date();
  // Move to the first day of the NEXT month.
  let year = now.getUTCFullYear();
  let month = now.getUTCMonth() + 1; // 0-indexed → next month
  if (month > 11) { month = 0; year += 1; }
  // First Friday: Jan 1 2026 is a Thursday (day 4). Walk until Friday (5).
  let day = 1;
  const firstDay = new Date(Date.UTC(year, month, 1)).getUTCDay();
  const daysToFriday = (5 - firstDay + 7) % 7;
  day += daysToFriday;
  // Third Friday = first Friday + 14 days.
  day += 14;
  const iso = new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
  return iso;
}

const OptionsPaperTrade = ({ row, defaultSide = 'buy' }) => {
  const parsed = useMemo(() => parseContract(row?.price), [row]);
  const [isOpen, setIsOpen] = useState(false);
  const [side, setSide] = useState(defaultSide);
  const [qty, setQty] = useState(1);
  const [expiry, setExpiry] = useState(defaultMonthlyExpiry);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  if (!parsed) return null; // malformed scanner row — hide gracefully

  const sideLabel = side === 'buy' ? 'Buy to Open' : 'Sell to Close';
  const ocsSide = side === 'buy' ? 'buy_to_open' : 'sell_to_close';

  const openModal = (forcedSide) => {
    setSide(forcedSide);
    setResult(null);
    setIsOpen(true);
  };

  const submit = async () => {
    setLoading(true);
    setResult(null);
    try {
      const res = await axios.post(
        `${API}/paper/trade`,
        {
          symbol: row.contract,
          side: ocsSide,
          qty,
          option: {
            strike: parsed.strike,
            expiry,
            type: parsed.type,
            iv_percent: typeof row.ivRank === 'number' ? row.ivRank : null,
          },
        },
        { withCredentials: true },
      );
      setResult({ ok: true, data: res.data });
      setTimeout(() => setIsOpen(false), 2500);
    } catch (err) {
      logger.error('options paper trade failed', err);
      setResult({
        ok: false,
        message: err.response?.data?.detail || 'Trade rejected',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={() => openModal('buy')}
          data-testid={`options-paper-buy-${row.contract}`}
          className="bg-green-600 hover:bg-green-700 flex items-center justify-between gap-1.5 text-xs min-w-[112px]"
        >
          <span className="flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5" />
            Buy
          </span>
          <TradeModePill mode="paper" />
        </Button>
        <Button
          size="sm"
          onClick={() => openModal('sell')}
          data-testid={`options-paper-sell-${row.contract}`}
          className="bg-red-600 hover:bg-red-700 flex items-center justify-between gap-1.5 text-xs min-w-[112px]"
        >
          <span className="flex items-center gap-1.5">
            <TrendingDown className="w-3.5 h-3.5" />
            Sell
          </span>
          <TradeModePill mode="paper" />
        </Button>
      </div>

      {isOpen && (
        <div
          className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4"
          data-testid="options-paper-modal"
        >
          <Card className="bg-slate-800 border-slate-400/20 rounded-xl p-6 w-full max-w-md">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-white text-xl font-bold">
                    {sideLabel}
                  </h3>
                  <TradeModePill mode="paper" size="md" />
                </div>
                <p className="text-slate-300 text-sm mt-1">
                  {row.contract} ${parsed.strike} {parsed.type.toUpperCase()}
                </p>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="text-slate-400 hover:text-white text-2xl leading-none"
              >×</button>
            </div>

            {/* Mode coherence banner — surfaces if user is in LIVE mode
                while opening this paper modal. */}
            <div className="mb-4">
              <TradingModeBanner expectedMode="paper" compact />
            </div>

            <div className="space-y-4">
              <div>
                <Label className="text-white mb-1 block">Contracts</Label>
                <Input
                  type="number"
                  min="1"
                  step="1"
                  value={qty}
                  onChange={(e) => setQty(Math.max(1, parseInt(e.target.value || '1', 10)))}
                  className="bg-slate-900 border-slate-600 text-white"
                  data-testid="options-paper-qty-input"
                />
                <p className="text-[11px] text-slate-400 mt-1">
                  1 contract = 100 shares of underlying
                </p>
              </div>

              <div>
                <Label className="text-white mb-1 block">Expiry</Label>
                <Input
                  type="date"
                  value={expiry}
                  onChange={(e) => setExpiry(e.target.value)}
                  className="bg-slate-900 border-slate-600 text-white"
                  data-testid="options-paper-expiry-input"
                />
                <p className="text-[11px] text-slate-400 mt-1">
                  Defaults to next monthly (3rd Friday)
                </p>
              </div>

              <div className="bg-slate-900 border border-slate-700 rounded p-3 flex items-start gap-2">
                <Info className="w-4 h-4 text-[#3DE8D9] flex-shrink-0 mt-0.5" />
                <p className="text-[11px] text-slate-300 leading-relaxed">
                  Fill is simulated via Black-Scholes on the current
                  underlying price and IV rank. Live options trading
                  against your broker is coming later — this is practice
                  only.
                </p>
              </div>
            </div>

            {result && (
              <div
                className={`mt-4 p-3 rounded flex items-start gap-2 text-sm ${
                  result.ok
                    ? 'bg-emerald-900/30 border border-emerald-700'
                    : 'bg-red-900/30 border border-red-700'
                }`}
                data-testid="options-paper-result"
              >
                <AlertCircle
                  className={`w-4 h-4 flex-shrink-0 mt-0.5 ${
                    result.ok ? 'text-emerald-300' : 'text-red-300'
                  }`}
                />
                <div className={result.ok ? 'text-emerald-200' : 'text-red-200'}>
                  {result.ok ? (
                    <>
                      <div className="font-semibold">Filled (paper)</div>
                      <div className="text-xs mt-0.5">
                        {result.data.qty}× {row.contract} ${parsed.strike}{' '}
                        {parsed.type.toUpperCase()} @ ${result.data.fill_price} · total
                        ${result.data.contract_cost}
                      </div>
                    </>
                  ) : (
                    <div>{result.message}</div>
                  )}
                </div>
              </div>
            )}

            <div className="flex gap-2 mt-5">
              <Button
                variant="outline"
                onClick={() => setIsOpen(false)}
                className="flex-1 bg-slate-900 border-slate-600 text-white hover:bg-slate-700"
              >
                Cancel
              </Button>
              <Button
                onClick={submit}
                disabled={loading || !qty || !expiry}
                className={`flex-1 ${
                  side === 'buy'
                    ? 'bg-green-600 hover:bg-green-700'
                    : 'bg-red-600 hover:bg-red-700'
                }`}
                data-testid="options-paper-submit-btn"
              >
                {loading ? 'Placing…' : `Paper ${sideLabel}`}
              </Button>
            </div>
          </Card>
        </div>
      )}
    </>
  );
};

export default OptionsPaperTrade;
