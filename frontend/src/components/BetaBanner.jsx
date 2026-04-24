/**
 * BetaBanner — amber "public beta" flag at the very top of the
 * landing page, above the navbar. Persistent (no dismiss).
 *
 * Live metadata:
 *   - seats_remaining from /api/beta/stats (drives urgency)
 *   - most-recent joiner's first name from /api/beta/recent
 *     (social proof — flips momentum into the copy once people
 *     start joining)
 *
 * Fails quiet: banner still renders the core copy even if either
 * endpoint is down.
 */
import React, { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api/beta`;

const BetaBanner = ({ onClaim }) => {
  const [stats, setStats] = useState(null);
  const [recent, setRecent] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch(`${API}/stats`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!cancelled) setStats(data);
        })
        .catch(() => {});
      fetch(`${API}/recent`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!cancelled) setRecent(data);
        })
        .catch(() => {});
    };
    load();
    // Gentle poll every 60s so the banner stays fresh without
    // hammering the API. Stops on unmount.
    const id = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const remaining = stats?.seats_remaining;
  const isFull = stats?.is_full === true;
  const hasJoiner = recent?.first_name && recent?.seat_number;

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
          <Sparkles className="w-4 h-4 shrink-0" />
          <span className="text-xs sm:text-sm font-semibold">
            {hasJoiner ? (
              <span data-testid="beta-banner-recent">
                🎉 {recent.first_name} just claimed seat #{recent.seat_number} —
                Pro + 30k credits for the First 50
              </span>
            ) : (
              <span data-testid="beta-banner-default">
                🚀 We're in public beta — Pro access + 30k credits for the First 50
              </span>
            )}
          </span>
          <span
            className="hidden sm:inline-flex items-center text-[9px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border border-slate-950/40 bg-slate-950/10 text-slate-950"
            title="U.S. Provisional Patents — App #64/047,926 (04/23/2026) & App #64/048,466 (04/24/2026)"
            data-testid="beta-banner-patent-pill"
          >
            2× Patents Pending
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
            {isFull ? 'Cohort full' : 'Claim my seat →'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default BetaBanner;
