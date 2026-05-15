/**
 * OptionsTradeButton — single "Trade" button per options scanner row.
 *
 * Replaces the old 4-button stack (Buy Paper / Sell Paper / Buy Live /
 * Sell Live) with one compact button that opens a small chooser sheet.
 * The chooser delegates to the existing OptionsPaperTrade and
 * OptionsLiveTrade modals — we don't duplicate the qty / expiry /
 * ODD-gate / broker-probe logic, we just imperatively trigger them.
 *
 * Mobile UX win: 4 buttons (with their own labels + pills) consumed
 * the entire row on a phone. One button keeps the actual market data
 * visible.
 */
import React, { useRef, useState, useEffect, useCallback } from 'react';
import { TrendingUp, TrendingDown, X } from 'lucide-react';
import { Button } from './ui/button';
import { TradeModePill } from './ui/TradeModePill';
import OptionsPaperTrade from './OptionsPaperTrade';
import OptionsLiveTrade from './OptionsLiveTrade';

const OptionsTradeButton = ({ row }) => {
  const [chooserOpen, setChooserOpen] = useState(false);
  const paperRef = useRef(null);
  const liveRef = useRef(null);

  // Close the chooser when ESC is pressed — matches modal convention.
  useEffect(() => {
    if (!chooserOpen) return;
    const onKey = (e) => { if (e.key === 'Escape') setChooserOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [chooserOpen]);

  const pick = useCallback((mode, side) => {
    setChooserOpen(false);
    // Defer to next tick so the chooser sheet unmounts before the
    // child modal mounts — avoids a brief double-overlay flash on
    // mobile.
    setTimeout(() => {
      const target = mode === 'paper' ? paperRef.current : liveRef.current;
      target?.open(side);
    }, 0);
  }, []);

  return (
    <>
      <Button
        size="sm"
        onClick={() => setChooserOpen(true)}
        data-testid={`options-trade-${row.contract}`}
        className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-slate-900 font-semibold text-xs px-4 py-1.5"
      >
        Trade
      </Button>

      {/* Children render invisibly — we drive their modals via refs. */}
      <OptionsPaperTrade ref={paperRef} row={row} hideTriggers />
      <OptionsLiveTrade  ref={liveRef}  row={row} hideTriggers />

      {chooserOpen && (
        <div
          className="fixed inset-0 bg-black/70 z-[55] flex items-end sm:items-center justify-center p-4"
          onClick={() => setChooserOpen(false)}
          data-testid="options-trade-chooser"
        >
          <div
            className="w-full max-w-sm bg-slate-800 border border-slate-400/30 rounded-2xl p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between mb-4">
              <div>
                <h3 className="text-white text-base font-semibold">Trade {row.contract}</h3>
                {row.price && (
                  <p className="text-slate-400 text-xs mt-0.5">{row.price}</p>
                )}
              </div>
              <button
                onClick={() => setChooserOpen(false)}
                className="text-slate-400 hover:text-white p-1 -mr-1 -mt-1"
                aria-label="Close"
                data-testid="options-trade-chooser-close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <Button
                onClick={() => pick('paper', 'buy')}
                data-testid={`options-trade-buy-paper-${row.contract}`}
                className="bg-green-600 hover:bg-green-700 flex items-center justify-between gap-2 text-xs h-10"
              >
                <span className="flex items-center gap-1.5">
                  <TrendingUp className="w-3.5 h-3.5" />
                  Buy
                </span>
                <TradeModePill mode="paper" />
              </Button>
              <Button
                onClick={() => pick('paper', 'sell')}
                data-testid={`options-trade-sell-paper-${row.contract}`}
                className="bg-red-600 hover:bg-red-700 flex items-center justify-between gap-2 text-xs h-10"
              >
                <span className="flex items-center gap-1.5">
                  <TrendingDown className="w-3.5 h-3.5" />
                  Sell
                </span>
                <TradeModePill mode="paper" />
              </Button>
              <Button
                onClick={() => pick('live', 'buy')}
                data-testid={`options-trade-buy-live-${row.contract}`}
                className="bg-green-600 hover:bg-green-700 flex items-center justify-between gap-2 text-xs h-10"
              >
                <span className="flex items-center gap-1.5">
                  <TrendingUp className="w-3.5 h-3.5" />
                  Buy
                </span>
                <TradeModePill mode="live" />
              </Button>
              <Button
                onClick={() => pick('live', 'sell')}
                data-testid={`options-trade-sell-live-${row.contract}`}
                className="bg-red-600 hover:bg-red-700 flex items-center justify-between gap-2 text-xs h-10"
              >
                <span className="flex items-center gap-1.5">
                  <TrendingDown className="w-3.5 h-3.5" />
                  Sell
                </span>
                <TradeModePill mode="live" />
              </Button>
            </div>

            <p className="text-[11px] text-slate-500 mt-3 text-center">
              PAPER simulates fills. LIVE routes to your broker (ODD required).
            </p>
          </div>
        </div>
      )}
    </>
  );
};

export default OptionsTradeButton;
