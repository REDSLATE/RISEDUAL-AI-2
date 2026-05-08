import React, { useEffect, useState } from 'react';
import { Trophy, Users, Crown } from 'lucide-react';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api/analytics`;

/**
 * ShareBoardLeaderboard — compact card showing:
 *   • The current user's ref + total hits + rank (if authenticated)
 *   • Global top 5 sharers (anonymized to "u{id-suffix}")
 *
 * Tracks hits from the "Share My Smart Money Board" flow (share-u{id} refs),
 * distinct from the app-wide referral/invite system in ReferralLeaderboard.jsx.
 */
export default function ShareBoardLeaderboard({ authenticated = true }) {
  const [me, setMe] = useState(null);
  const [top, setTop] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const topRes = await fetch(`${API}/ref-leaderboard?limit=5`, { credentials: 'include' });
        if (topRes.ok) setTop((await topRes.json()).leaderboard || []);
        if (authenticated) {
          const meRes = await authFetch(`${API}/ref-me`);
          if (meRes.ok) setMe(await meRes.json());
        }
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [authenticated]);

  if (loading) return null;

  const hasData = (me?.hits ?? 0) > 0 || top.length > 0;
  if (!hasData) return null;

  const rewardBadge = me?.recent_reward ? (() => {
    const r = me.recent_reward;
    const label = r.kind === 'trial_pro_max' ? `Pro Max · ${r.amount}d`
      : r.kind === 'trial_pro' ? `Pro · ${r.amount}d`
      : r.kind === 'credits' ? `${r.amount} credits`
      : r.tier;
    return (
      <span
        className="ml-2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-[#3DE8D9]/40 bg-[#3DE8D9]/10 text-[#3DE8D9] text-[9px] font-bold"
        title={`You earned: ${r.tier} (${r.period || 'current'})`}
        data-testid="share-board-reward-badge"
      >
        🏆 {label}
      </span>
    );
  })() : null;

  return (
    <div
      className="mt-3 rounded-lg border border-slate-700/50 bg-slate-900/40 p-3 space-y-2"
      data-testid="share-board-leaderboard"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <Trophy className="w-3.5 h-3.5 text-[#3DE8D9]" />
        <span className="text-white text-xs font-semibold">Share Leaderboard</span>
        {rewardBadge}
        {me?.rank != null && (
          <span className="ml-auto text-[10px] text-slate-400">
            Your rank: <span className="text-white font-bold">#{me.rank}</span>
            <span className="text-slate-500"> · {me.hits ?? 0} {me.hits === 1 ? 'hit' : 'hits'}</span>
          </span>
        )}
      </div>

      {top.length === 0 ? (
        <p className="text-slate-500 text-[11px] italic">No shares tracked yet — share your board to be the first on the leaderboard.</p>
      ) : (
        <div className="space-y-1">
          {top.map((row) => (
            <div
              key={row.ref}
              className={`flex items-center gap-2 text-[11px] px-2 py-1 rounded ${row.rank === 1 ? 'bg-[#3DE8D9]/5 border border-[#3DE8D9]/20' : ''}`}
              data-testid={`share-board-leaderboard-row-${row.rank}`}
            >
              <span className={`font-bold tabular-nums w-5 ${row.rank === 1 ? 'text-[#3DE8D9]' : 'text-slate-400'}`}>
                #{row.rank}
              </span>
              {row.rank === 1 && <Crown className="w-3 h-3 text-[#3DE8D9] -ml-1" />}
              <span className="flex-1 font-mono text-slate-300 truncate">
                {row.user_suffix ? `u${row.user_suffix}` : row.ref.replace('share-', '')}
              </span>
              <span className="flex items-center gap-1 text-white font-semibold">
                <Users className="w-3 h-3 text-slate-500" /> {row.hits}
              </span>
            </div>
          ))}
        </div>
      )}

      <p className="text-slate-600 text-[9px] text-center pt-1">
        Tracks anonymous visitor hits from shared boards · Last 90 days<br />
        <span className="text-slate-500">#1 earns 30d Pro Max · #2-3 earn 30d Pro · #4-5 earn 100 credits · 5+ hits any month = 7d Pro</span>
      </p>
    </div>
  );
}
