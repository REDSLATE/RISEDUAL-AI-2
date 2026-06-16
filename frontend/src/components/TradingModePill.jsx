import React from 'react';
import { Flame } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

/**
 * TradingModePill — 2026-06-16 rewrite.
 *
 * Operator directive: paper trading removed (Public.com + Kraken have
 * no paper sandboxes). The Paper/Live toggle is therefore retired and
 * replaced by a static "LIVE" badge identifying the active broker set.
 *
 * The component still renders a pill in the navbar slot so the layout
 * is unchanged, and it preserves the same data-testid so any e2e
 * selector wiring keeps working. Click is now a no-op tooltip — there
 * is nothing to toggle.
 */
const TradingModePill = () => {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <div
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider border bg-orange-500/15 text-orange-300 border-orange-500/40 shadow-[0_0_12px_rgba(249,115,22,0.25)] cursor-default select-none"
      title="LIVE trading only. Routed to Public.com (equity) + Kraken (crypto)."
      data-testid="trading-mode-pill"
      data-mode="live"
    >
      <Flame className="w-3 h-3" />
      <span>LIVE · Public + Kraken</span>
    </div>
  );
};

export default TradingModePill;
