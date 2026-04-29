/**
 * OptionsLiveTrade — live-broker options Buy/Sell, ODD-gated.
 *
 * Companion to `OptionsPaperTrade.jsx`. Same scanner-row shape, but
 * renders a bright RED "LIVE" badge and routes to `/api/options/*`
 * instead of the paper path.
 *
 * Flow:
 *   1. If user hasn't accepted ODD → show "Review & Accept"
 *      disclosure modal first. Only after accept do we proceed.
 *   2. Show options-enabled probe (buying power, approved level).
 *      If provider returns `enabled=false`, render a clear "your
 *      broker doesn't have options enabled" state.
 *   3. Confirmation modal with underlying, strike, expiry picker,
 *      qty, limit-price toggle, and a big red LIVE button.
 *   4. On submit → POST `/api/options/order`.
 *
 * Deliberately terse — pulls everything from the adapter, no local
 * validation beyond "qty > 0". Broker is the source of truth for
 * buying power and options-eligibility.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, AlertCircle, ShieldAlert } from 'lucide-react';
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

function parseContract(priceField) {
  if (!priceField || typeof priceField !== 'string') return null;
  const m = priceField.match(/\$([\d.]+)\s+(Call|Put)/i);
  if (!m) return null;
  return { strike: parseFloat(m[1]), type: m[2].toLowerCase() };
}

function defaultMonthlyExpiry() {
  const now = new Date();
  let year = now.getUTCFullYear();
  let month = now.getUTCMonth() + 1;
  if (month > 11) { month = 0; year += 1; }
  const firstDay = new Date(Date.UTC(year, month, 1)).getUTCDay();
  const daysToFriday = (5 - firstDay + 7) % 7;
  const day = 1 + daysToFriday + 14;
  return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
}

const OptionsLiveTrade = ({ row, defaultSide = 'buy' }) => {
  const parsed = useMemo(() => parseContract(row?.price), [row]);

  const [isOpen, setIsOpen] = useState(false);
  const [side, setSide] = useState(defaultSide);
  const [qty, setQty] = useState(1);
  const [expiry, setExpiry] = useState(defaultMonthlyExpiry);
  const [orderType, setOrderType] = useState('market');
  const [limitPrice, setLimitPrice] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [bestExecution, setBestExecution] = useState(false);

  // ODD + broker status, fetched when the modal opens.
  const [oddAccepted, setOddAccepted] = useState(null);
  const [brokerStatus, setBrokerStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);

  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const [oddRes, statusRes] = await Promise.all([
        axios.get(`${API}/options/odd/status`, { withCredentials: true }),
        axios.get(`${API}/options/status`, { withCredentials: true }).catch(e => ({ data: { enabled: false, error: e.response?.data?.detail } })),
      ]);
      setOddAccepted(oddRes.data.accepted);
      setBrokerStatus(statusRes.data);
    } catch (err) {
      logger.error('options live status fetch failed', err);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) refreshStatus();
  }, [isOpen, refreshStatus]);

  if (!parsed) return null;

  const sideLabel = side === 'buy' ? 'Buy to Open' : 'Sell to Close';
  const ocsSide = side === 'buy' ? 'buy_to_open' : 'sell_to_close';

  const openModal = (forcedSide) => {
    setSide(forcedSide);
    setResult(null);
    setIsOpen(true);
  };

  const acceptODD = async () => {
    try {
      await axios.post(`${API}/options/odd/accept`, { accept: true }, { withCredentials: true });
      setOddAccepted(true);
    } catch (err) {
      logger.error('odd accept failed', err);
      setResult({ ok: false, message: 'Could not record ODD acceptance — try again.' });
    }
  };

  const submit = async () => {
    setLoading(true);
    setResult(null);
    try {
      const body = {
        underlying: row.contract,
        strike: parsed.strike,
        expiry,
        option_type: parsed.type,
        side: ocsSide,
        qty,
        order_type: orderType,
        time_in_force: 'day',
        best_execution: bestExecution,
      };
      if (orderType === 'limit' && limitPrice) {
        body.limit_price = parseFloat(limitPrice);
      }
      const res = await axios.post(`${API}/options/order`, body, { withCredentials: true });
      setResult({ ok: true, data: res.data });
    } catch (err) {
      logger.error('options live trade failed', err);
      setResult({
        ok: false,
        message: err.response?.data?.detail || 'Order rejected by broker',
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
          data-testid={`options-live-buy-${row.contract}`}
          className="bg-green-600 hover:bg-green-700 flex items-center justify-between gap-1.5 text-xs min-w-[112px]"
        >
          <span className="flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5" />
            Buy
          </span>
          <TradeModePill mode="live" />
        </Button>
        <Button
          size="sm"
          onClick={() => openModal('sell')}
          data-testid={`options-live-sell-${row.contract}`}
          className="bg-red-600 hover:bg-red-700 flex items-center justify-between gap-1.5 text-xs min-w-[112px]"
        >
          <span className="flex items-center gap-1.5">
            <TrendingDown className="w-3.5 h-3.5" />
            Sell
          </span>
          <TradeModePill mode="live" />
        </Button>
      </div>

      {isOpen && (
        <div
          className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4"
          data-testid="options-live-modal"
        >
          <Card className="bg-slate-800 border-red-500/40 rounded-xl p-6 w-full max-w-md">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-white text-xl font-bold">{sideLabel}</h3>
                  <TradeModePill mode="live" size="md" />
                </div>
                <p className="text-slate-300 text-sm mt-1">
                  {row.contract} ${parsed.strike} {parsed.type.toUpperCase()}
                </p>
              </div>
              <button onClick={() => setIsOpen(false)} className="text-slate-400 hover:text-white text-2xl leading-none">×</button>
            </div>

            {/* Trading-mode coherence — banner shows up when the user
                opens this LIVE trade modal while in PAPER mode. */}
            <div className="mb-4">
              <TradingModeBanner expectedMode="live" compact />
            </div>

            {/* ── Status / ODD gate ── */}
            {statusLoading && (
              <div className="text-slate-400 text-sm py-3">Checking broker status…</div>
            )}

            {!statusLoading && oddAccepted === false && (
              <div className="bg-amber-900/30 border border-amber-500/40 rounded p-3 mb-4">
                <div className="flex items-start gap-2">
                  <ShieldAlert className="w-5 h-5 text-amber-300 flex-shrink-0 mt-0.5" />
                  <div className="text-xs text-amber-200">
                    <div className="font-semibold mb-1">Options Disclosure Required</div>
                    <p className="mb-2">
                      Before placing live options orders, you must review the
                      Options Clearing Corporation (OCC) disclosure document at{' '}
                      <a
                        href="https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document"
                        target="_blank" rel="noopener noreferrer"
                        className="text-[#3DE8D9] underline"
                      >theocc.com</a>
                      {' '}and acknowledge the risks of options trading.
                    </p>
                    <Button
                      size="sm"
                      onClick={acceptODD}
                      data-testid="options-live-odd-accept-btn"
                      className="bg-amber-500 hover:bg-amber-400 text-slate-900 text-xs"
                    >
                      I've reviewed & accept the ODD
                    </Button>
                  </div>
                </div>
              </div>
            )}

            {!statusLoading && brokerStatus && !brokerStatus.enabled && (
              <div className="bg-red-900/30 border border-red-500/40 rounded p-3 mb-4 flex items-start gap-2">
                <AlertCircle className="w-5 h-5 text-red-300 flex-shrink-0 mt-0.5" />
                <div className="text-xs text-red-200">
                  <div className="font-semibold mb-1">
                    {brokerStatus.provider || 'Broker'} options not enabled
                  </div>
                  <p>{brokerStatus.details || brokerStatus.error || 'Your account does not have options trading enabled.'}</p>
                </div>
              </div>
            )}

            {/* ── Order form (only when ODD accepted + broker enabled) ── */}
            {oddAccepted && brokerStatus?.enabled && (
              <div className="space-y-4">
                <div>
                  <Label className="text-white mb-1 block">Contracts</Label>
                  <Input
                    type="number" min="1" step="1"
                    value={qty}
                    onChange={(e) => setQty(Math.max(1, parseInt(e.target.value || '1', 10)))}
                    className="bg-slate-900 border-slate-600 text-white"
                    data-testid="options-live-qty-input"
                  />
                  <p className="text-[11px] text-slate-400 mt-1">
                    1 contract = 100 shares. Level {brokerStatus.level} enabled.
                  </p>
                </div>

                <div>
                  <Label className="text-white mb-1 block">Expiry</Label>
                  <Input
                    type="date" value={expiry}
                    onChange={(e) => setExpiry(e.target.value)}
                    className="bg-slate-900 border-slate-600 text-white"
                    data-testid="options-live-expiry-input"
                  />
                </div>

                <div>
                  <Label className="text-white mb-1 block">Order Type</Label>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant={orderType === 'market' ? 'default' : 'outline'}
                      onClick={() => setOrderType('market')}
                      className="flex-1"
                    >Market</Button>
                    <Button
                      type="button"
                      variant={orderType === 'limit' ? 'default' : 'outline'}
                      onClick={() => setOrderType('limit')}
                      className="flex-1"
                    >Limit</Button>
                  </div>
                  {orderType === 'limit' && (
                    <Input
                      type="number" step="0.01" min="0.01"
                      placeholder="Limit price"
                      value={limitPrice}
                      onChange={(e) => setLimitPrice(e.target.value)}
                      className="bg-slate-900 border-slate-600 text-white mt-2"
                      data-testid="options-live-limit-input"
                    />
                  )}
                </div>

                <div className="bg-slate-900 border border-red-500/20 rounded p-3 text-[11px] text-slate-300">
                  <strong className="text-red-300">Live order.</strong>{' '}
                  This will submit a real order to {brokerStatus.provider}.
                  Executed trades are binding.
                </div>
              </div>
            )}

            {result && (
              <div
                className={`mt-4 p-3 rounded text-sm ${
                  result.ok
                    ? 'bg-emerald-900/30 border border-emerald-700 text-emerald-200'
                    : 'bg-red-900/30 border border-red-700 text-red-200'
                }`}
                data-testid="options-live-result"
              >
                {result.ok ? (
                  <>
                    <div className="font-semibold">Order submitted to {result.data.provider}</div>
                    <div className="text-xs mt-0.5">
                      #{result.data.order_id?.slice(0, 8)}… · status: {result.data.status}
                    </div>
                  </>
                ) : (
                  <div>{result.message}</div>
                )}
              </div>
            )}

            <div className="flex gap-2 mt-5">
              <Button
                variant="outline"
                onClick={() => setIsOpen(false)}
                className="flex-1 bg-slate-900 border-slate-600 text-white hover:bg-slate-700"
              >Cancel</Button>
              {oddAccepted && brokerStatus?.enabled && (
                <Button
                  onClick={submit}
                  disabled={loading || !qty || !expiry || (orderType === 'limit' && !limitPrice)}
                  className={`flex-1 ${side === 'buy' ? 'bg-green-600 hover:bg-green-700' : 'bg-red-600 hover:bg-red-700'}`}
                  data-testid="options-live-submit-btn"
                >
                  {loading ? 'Submitting…' : `LIVE ${sideLabel}`}
                </Button>
              )}
            </div>
          </Card>
        </div>
      )}
    </>
  );
};

export default OptionsLiveTrade;
