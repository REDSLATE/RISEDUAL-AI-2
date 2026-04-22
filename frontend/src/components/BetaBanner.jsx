/**
 * BetaBanner — amber "public beta" flag that sits at the very top of
 * the landing page, above the navbar. Persistent (no dismiss) per
 * product direction: beta status is a narrative, not a nag.
 *
 * Pulls live seat count from /api/beta/stats so the urgency is real,
 * not hardcoded. Fails quiet — banner still renders with the copy
 * even if the counter can't load.
 */
import React, { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api/beta`;

const BetaBanner = ({ onClaim }) => {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/stats`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const remaining = stats?.seats_remaining;
  const isFull = stats?.is_full === true;

  return (
    <div
      className="relative z-[60] w-full bg-gradient-to-r from-amber-600 via-yellow-500 to-amber-600 text-slate-950 border-b border-amber-300/50 shadow-[0_2px_8px_rgba(251,191,36,0.25)]"
      data-testid="beta-banner"
    >
      {/* Barber-pole stripe for a "caution / beta flag" feel. */}
      <div
        className="absolute inset-0 opacity-10 pointer-events-none"
        style={{
          backgroundImage:
            'repeating-linear-gradient(45deg, rgba(0,0,0,0.4) 0px, rgba(0,0,0,0.4) 10px, transparent 10px, transparent 20px)',
        }}
      />
      <div className="relative max-w-7xl mx-auto px-4 sm:px-6 py-2 flex flex-col sm:flex-row items-center justify-center gap-2 sm:gap-4 text-center">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4" />
          <span className="text-xs sm:text-sm font-semibold">
            🚀 We're in public beta — your feedback shapes what ships next
          </span>
        </div>
        <div className="flex items-center gap-2">
          {typeof remaining === 'number' && !isFull && (
            <span
              className="text-[10px] sm:text-xs bg-slate-950/20 border border-slate-950/30 rounded-full px-2 py-0.5 font-bold tabular-nums"
              data-testid="beta-banner-seats"
            >
              {remaining} of {stats.cap} seats left
            </span>
          )}
          <button
            onClick={onClaim}
            disabled={isFull}
            className="text-xs sm:text-sm font-bold bg-slate-950 text-amber-300 hover:bg-slate-800 disabled:bg-slate-700 disabled:text-slate-400 disabled:cursor-not-allowed rounded-full px-3 sm:px-4 py-1 transition-colors"
            data-testid="beta-banner-cta"
          >
            {isFull ? 'Cohort full' : 'Claim a seat →'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default BetaBanner;
