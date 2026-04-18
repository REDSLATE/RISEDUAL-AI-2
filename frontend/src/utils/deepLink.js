// Unified "Level-2 deep-link" helper used by every surface that wants to route
// a user to the AI War Room for a given ticker AND log adoption telemetry.
//
// Callers: SectorTile, CryptoTile, Watchlist (per-row + SM shift alerts),
// FearGreedGauge verdict tab.
//
// Keeping this in one place ensures every click fires:
//   1. POST /api/analytics/chip-event  (fire-and-forget, silent on failure)
//   2. window CustomEvent "risedualai-navigate"  → App.js routes to the hub
//   3. window CustomEvent "risedualai-warroom"  → AIWarRoom/Hypothesis/
//      MarketPrediction listen and auto-run the analysis
//   4. Toast notification with Undo button — snapshots the current view and
//      lets the user pop back if they mis-clicked.
//
// Changing behaviour (e.g. adding a fifth step, new telemetry field) is now a
// single-file edit.
import { toast } from 'sonner';
import { getApiBase } from './apiBase';

const API = `${getApiBase()}/api`;

const getActiveHub = () => {
  try {
    return typeof window !== 'undefined' ? (window.__risedualActiveView || null) : null;
  } catch {
    return null;
  }
};

const logChip = (label) => {
  try {
    fetch(`${API}/analytics/chip-event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        action: 'action-clicked',
        chip_text: label,
        context_hub: getActiveHub(),
      }),
    }).catch((e) => {
      // eslint-disable-next-line no-console
      console.debug('chip telemetry fetch failed:', e);
    });
  } catch (e) {
    // eslint-disable-next-line no-console
    console.debug('logChip dispatch failed:', e);
  }
};

/**
 * Open the AI War Room for a ticker from any surface.
 *
 * @param {Object} opts
 * @param {string} opts.ticker   — ticker symbol (e.g. 'NVDA', 'SPY', 'XLK', 'BTC'). Required.
 * @param {string} opts.source   — human-readable source label used in telemetry
 *                                 chip_text (e.g. 'Sector Heatmap',
 *                                 'Crypto Heatmap', 'SM Shift Alert').
 * @param {string} [opts.subTab] — War Room sub-tab key: 'adversarial' |
 *                                 'prediction' | 'hypothesis'. Defaults to
 *                                 'adversarial'.
 * @param {string} [opts.suffix] — extra context appended to telemetry label
 *                                 (e.g. '(GREED 68)' for Fear & Greed regime).
 */
export const openWarRoomForTicker = ({ ticker, source, subTab = 'adversarial', suffix = '' }) => {
  if (!ticker) return;
  const t = String(ticker).toUpperCase().trim();
  if (!t) return;
  const suffixStr = suffix ? ` ${suffix}` : '';
  logChip(`Open ${t} War Room (${source}${suffixStr})`);

  // Snapshot the origin view BEFORE we nav so the Undo action can restore it.
  const prevView = getActiveHub();

  try {
    window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: 'warroom', subTab } }));
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent('risedualai-warroom', { detail: t }));
    }, 180);
  } catch (e) {
    // eslint-disable-next-line no-console
    console.debug('openWarRoomForTicker dispatch failed:', e);
  }

  // Undo toast — non-intrusive, auto-dismisses in 5s, snapshots origin view.
  // Don't show the toast if the user was already on /warroom (no meaningful
  // "back" state).
  if (prevView && prevView !== 'warroom') {
    try {
      toast(`Analyzing ${t}`, {
        description: `From ${source}`,
        duration: 5000,
        action: {
          label: 'Undo',
          onClick: () => {
            try {
              // Route back to where they were (and dismiss any in-flight
              // ticker run by re-navigating to the origin view).
              logChip(`Undo ${t} War Room (${source})`);
              window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: prevView } }));
            } catch (e) {
              // eslint-disable-next-line no-console
              console.debug('Undo nav failed:', e);
            }
          },
        },
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.debug('Undo toast failed:', e);
    }
  }
};
