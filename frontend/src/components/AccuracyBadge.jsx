import React, { useState, useEffect } from 'react';
import { Target } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

/**
 * Compact accuracy pill for a feature (war_room | hypothesis | market_prediction).
 *
 * Surfaces TWO numbers when both exist:
 *   - Directional accuracy  — BUY/SELL calls only (more honest for trading conviction)
 *   - Inclusive accuracy    — includes NEUTRAL/HOLD calls ("on the right side")
 *
 * The larger-font number on the pill is the DIRECTIONAL one. The inclusive
 * number appears as a small secondary text after a slash when present and
 * different from the directional value.
 */
const AccuracyBadge = ({ feature, className = '' }) => {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/accuracy/stats/${feature}`, {
          credentials: 'include',
        });
        if (res.ok) {
          const data = await res.json();
          setStats(data);
        }
      } catch {
        // Silently fail — user may not be Pro
      }
    };
    fetchStats();
  }, [feature]);

  if (!stats || (stats.total_24h === 0 && stats.total_1w === 0)) return null;

  const accDir = stats.accuracy_1w_directional;    // new — BUY/SELL only
  const accAll = stats.accuracy_1w;                 // all calls (incl. NEUTRAL)
  const acc24 = stats.accuracy_24h;
  // Primary display: prefer directional 1w (most honest), then inclusive 1w,
  // then 24h, then fall back to pending-only badge.
  const primary =
    accDir !== null && accDir !== undefined ? { val: accDir, tf: '1W·DIR', total: stats.total_1w_directional } :
    accAll !== null && accAll !== undefined ? { val: accAll, tf: '1W',     total: stats.total_1w } :
    acc24  !== null && acc24  !== undefined ? { val: acc24,  tf: '24H',    total: stats.total_24h } :
    null;

  if (!primary) {
    if (stats.pending > 0) {
      return (
        <span className={`inline-flex items-center gap-1 text-[10px] bg-slate-800/60 text-slate-400 px-2 py-0.5 rounded-full border border-slate-400/30/40 ${className}`} data-testid={`accuracy-badge-${feature}`}>
          <Target className="w-2.5 h-2.5" />
          {stats.pending} pending
        </span>
      );
    }
    return null;
  }

  // Show secondary inclusive number only when we're displaying directional
  // AND the inclusive value meaningfully differs (>0.5 pp).
  const showSecondary =
    primary.tf === '1W·DIR'
    && accAll !== null && accAll !== undefined
    && Math.abs(accAll - accDir) > 0.5;

  const color = primary.val >= 60 ? 'text-lime-400 border-lime-700/40 bg-green-600'
    : primary.val >= 50 ? 'text-amber-300 border-amber-800/40 bg-amber-900/20'
    : 'text-orange-400 border-orange-700/40 bg-orange-900';

  const tooltip = [
    `${primary.val.toFixed(1)}% on ${primary.total} ${primary.tf === '1W·DIR' ? 'directional (BUY/SELL) ' : ''}predictions (${primary.tf.replace('·DIR','')})`,
    showSecondary ? `Incl. NEUTRAL/HOLD: ${accAll.toFixed(1)}% on ${stats.total_1w}` : null,
    stats.pending ? `${stats.pending} still pending verification` : null,
  ].filter(Boolean).join(' · ');

  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${color} ${className}`}
      data-testid={`accuracy-badge-${feature}`}
      title={tooltip}
    >
      <Target className="w-2.5 h-2.5" />
      <span className="font-semibold tabular-nums">{primary.val.toFixed(1)}%</span>
      <span className="text-[9px] opacity-70">{primary.tf}</span>
      {showSecondary && (
        <span className="text-[9px] opacity-60 border-l border-current/20 pl-1 ml-0.5" data-testid={`accuracy-badge-${feature}-inclusive`}>
          {accAll.toFixed(1)}%
        </span>
      )}
    </span>
  );
};

export default AccuracyBadge;
