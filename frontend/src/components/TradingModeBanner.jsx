import React from 'react';
import { Beaker, Flame, AlertTriangle } from 'lucide-react';
import { useTradingMode } from '../hooks/useTradingMode';

/**
 * TradingModeBanner — sticky context banner for any order-entry panel.
 *
 * Two visual states:
 *
 *   1. **Match** (current mode == panel's expected mode):
 *      A small confirmation pill so the user knows they're in the
 *      right context. Mint for paper, orange for live.
 *
 *   2. **Mismatch** (panel expects the opposite mode):
 *      A bright warning row with a "Switch" CTA that opens the
 *      navbar TradingModePill modal — implemented as a click event
 *      since that's the only entry point that goes through the
 *      cooldown + audit pipeline.
 *
 * Props:
 *   • expectedMode  — "paper" | "live". The mode this panel needs.
 *   • compact       — when true, render a tight single-row variant
 *                     (used inside dense modals / forms).
 */
const TradingModeBanner = ({ expectedMode = 'paper', compact = false }) => {
  const { mode } = useTradingMode();
  const matches = mode === expectedMode;
  const isPaper = expectedMode === 'paper';

  const requestSwitch = () => {
    // Programmatic click on the navbar pill so the audit + cooldown
    // pipeline is the single source of truth for mode flips.
    const pill = document.querySelector('[data-testid="trading-mode-pill"]');
    if (pill) pill.click();
  };

  if (matches) {
    const Icon = isPaper ? Beaker : Flame;
    const colorClasses = isPaper
      ? 'text-[#3DE8D9] bg-[#3DE8D9]/10 border-[#3DE8D9]/25'
      : 'text-orange-300 bg-orange-500/10 border-orange-500/25';
    return (
      <div
        className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] font-bold uppercase tracking-wider ${colorClasses}`}
        data-testid={`trading-mode-banner-${expectedMode}-active`}
      >
        <Icon className="w-3 h-3" />
        <span>{expectedMode} mode</span>
      </div>
    );
  }

  // Mismatch — the user needs to switch before they can place orders.
  return (
    <div
      className={`flex items-start gap-3 rounded-lg border border-orange-500/40 bg-orange-500/10 ${
        compact ? 'px-3 py-2' : 'px-4 py-3'
      }`}
      data-testid={`trading-mode-banner-mismatch-${expectedMode}`}
      data-current-mode={mode}
    >
      <AlertTriangle className="w-4 h-4 text-orange-300 flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-orange-100 text-xs leading-snug">
          You are in <span className="font-bold uppercase">{mode}</span> mode.
          {' '}This panel needs <span className="font-bold uppercase">{expectedMode}</span>.
          Orders submitted from here will be rejected until you switch.
        </p>
      </div>
      <button
        type="button"
        onClick={requestSwitch}
        className="text-orange-200 text-xs font-semibold underline-offset-4 hover:text-orange-100 hover:underline whitespace-nowrap"
        data-testid={`trading-mode-banner-switch-${expectedMode}`}
      >
        Switch to {expectedMode.toUpperCase()}
      </button>
    </div>
  );
};

export default TradingModeBanner;
