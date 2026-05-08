import { useAuth } from '../contexts/AuthContext';

/**
 * useTradingMode — single source of truth for the user's PAPER/LIVE mode
 * across order-entry surfaces.
 *
 * Returns:
 *   • mode               — "paper" | "live" (defaults to "paper")
 *   • isLive / isPaper   — boolean shorthands for conditional rendering
 *   • requires(targetMode) — boolean: does the user need to switch?
 *
 * Components should read this instead of poking `user.trading_mode`
 * directly so we have a single seam to add shadow modes / per-asset
 * overrides later.
 */
export const useTradingMode = () => {
  const { user } = useAuth();
  const mode = user?.trading_mode || 'paper';
  return {
    mode,
    isLive: mode === 'live',
    isPaper: mode === 'paper',
    requires: (target) => mode !== target,
  };
};

export default useTradingMode;
