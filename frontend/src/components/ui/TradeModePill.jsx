/**
 * TradeModePill — unified PAPER / LIVE badge.
 *
 * Replaces the inline pill classes that were drifting across
 * OptionsPaperTrade, OptionsLiveTrade, and their modal headers.
 * Key decisions pinned here:
 *
 *   - LIVE uses a white pill with red-700 text + ring. Previously
 *     it was `bg-red-500 text-white`, which vanished against
 *     Sell buttons (`bg-red-600`) — the pill was effectively
 *     invisible. White-on-red keeps urgency while ensuring the
 *     pill reads cleanly on BOTH green and red button backgrounds.
 *   - Shared `min-w-[38px]` so PAPER and LIVE pills stack
 *     column-aligned on a Buy / Sell row grid.
 *   - `text-[10px]` (modal) vs `text-[9px]` (button) controlled via
 *     the `size` prop so both use-sites stay consistent.
 */
import React from 'react';

const STYLES = {
  paper: 'bg-amber-400 text-slate-900',
  live: 'bg-white text-red-700 ring-1 ring-red-400',
};

const SIZES = {
  sm: 'px-1.5 py-0.5 text-[9px] min-w-[38px]',
  md: 'px-2 py-0.5 text-[10px] min-w-[44px]',
};

export const TradeModePill = ({ mode = 'paper', size = 'sm', className = '' }) => {
  const modeClass = STYLES[mode] || STYLES.paper;
  const sizeClass = SIZES[size] || SIZES.sm;
  return (
    <span
      className={`inline-flex items-center justify-center font-bold rounded tracking-wide ${sizeClass} ${modeClass} ${className}`}
      data-testid={`trade-mode-pill-${mode}`}
    >
      {mode.toUpperCase()}
    </span>
  );
};

export default TradeModePill;
