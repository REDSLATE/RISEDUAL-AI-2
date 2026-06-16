import React from 'react';
import { Flame } from 'lucide-react';

/**
 * TradingModeBanner — 2026-06-16 rewrite.
 *
 * Paper trading is retired (Public.com + Kraken — no paper sandbox).
 * The mismatch / "switch mode" CTA branch no longer applies. Any
 * call site that asks for ``expectedMode="paper"`` now sees a clear
 * "LIVE only" indicator, and the order-entry path is already
 * server-gated to refuse paper.
 *
 * We keep the component (and the data-testid shape) so existing call
 * sites still render something sensible — they just always show the
 * live badge now.
 */
const TradingModeBanner = ({ expectedMode = 'live', compact = false }) => {
  const isPaperRequest = expectedMode === 'paper';

  if (isPaperRequest) {
    // Paper was requested but is no longer supported. Surface the
    // change explicitly so the operator notices a stale call site.
    return (
      <div
        className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-orange-300 bg-orange-500/10 border-orange-500/25`}
        data-testid="trading-mode-banner-paper-retired"
        title="Paper trading retired — all orders route live to Public.com + Kraken"
      >
        <Flame className="w-3 h-3" />
        <span>live only</span>
      </div>
    );
  }

  // Standard live indicator — the canonical, always-true state.
  return (
    <div
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-orange-300 bg-orange-500/10 border-orange-500/25 ${
        compact ? '' : 'px-2 py-1'
      }`}
      data-testid="trading-mode-banner-live-active"
    >
      <Flame className="w-3 h-3" />
      <span>live mode</span>
    </div>
  );
};

export default TradingModeBanner;
