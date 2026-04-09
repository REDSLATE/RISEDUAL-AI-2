import React, { useState, useEffect } from 'react';
import { Trophy, Users, Medal, ChevronUp, Flame, Share2 } from 'lucide-react';
import { Card } from './ui/card';
import { useAuth, authFetch } from '../contexts/AuthContext';
import SocialShareButtons from './SocialShareButtons';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = getApiBase();

const RANK_STYLES = [
  { bg: 'from-amber-500/20 to-yellow-600/10', border: 'border-amber-500/40', icon: 'text-amber-400', text: 'text-amber-300' },
  { bg: 'from-slate-300/15 to-gray-400/10', border: 'border-slate-400/40', icon: 'text-slate-300', text: 'text-slate-300' },
  { bg: 'from-orange-600/15 to-amber-700/10', border: 'border-orange-600/40', icon: 'text-orange-400', text: 'text-orange-300' },
];

const ReferralLeaderboard = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refCode, setRefCode] = useState(null);
  const { user } = useAuth();

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/referral/leaderboard`);
        if (res.ok) setData(await res.json());
      } catch (e) {
        logger.error('Leaderboard load error:', e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  useEffect(() => {
    if (!user) { setRefCode(null); return; }
    const loadCode = async () => {
      try {
        const res = await authFetch(`${API}/api/referral/info`);
        if (res.ok) {
          const info = await res.json();
          setRefCode(info.code);
        }
      } catch (e) { logger.error('Leaderboard share check failed:', e); }
    };
    loadCode();
  }, [user]);

  if (loading) {
    return (
      <Card className="bg-[#0F172A]/80 border-slate-700/40 rounded-2xl p-6" data-testid="leaderboard-loading">
        <div className="flex items-center gap-3 mb-4">
          <Trophy className="w-5 h-5 text-amber-400" />
          <h3 className="text-white font-semibold text-sm">Top Referrers</h3>
        </div>
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={`skel-${i}`} className="h-12 bg-slate-800/50 rounded-xl animate-pulse" />
          ))}
        </div>
      </Card>
    );
  }

  if (!data || data.leaderboard.length === 0) {
    return (
      <Card className="bg-[#0F172A]/80 border-slate-700/40 rounded-2xl p-6" data-testid="leaderboard-empty">
        <div className="flex items-center gap-3 mb-4">
          <Trophy className="w-5 h-5 text-amber-400" />
          <h3 className="text-white font-semibold text-sm">Top Referrers</h3>
        </div>
        <div className="text-center py-6">
          <Users className="w-8 h-8 text-slate-600 mx-auto mb-2" />
          <p className="text-slate-400 text-xs">Be the first to refer a friend!</p>
        </div>
      </Card>
    );
  }

  return (
    <Card className="bg-[#0F172A]/80 border-slate-700/40 rounded-2xl p-5" data-testid="referral-leaderboard">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-500/20 to-amber-600/10 flex items-center justify-center">
            <Trophy className="w-4.5 h-4.5 text-amber-400" />
          </div>
          <div>
            <h3 className="text-white font-semibold text-sm">Top Referrers</h3>
            <p className="text-slate-500 text-[10px]">{data.total_participants} participants</p>
          </div>
        </div>
        <Flame className="w-4 h-4 text-orange-500 animate-pulse" />
      </div>

      <div className="space-y-2">
        {data.leaderboard.map((entry, i) => {
          const style = RANK_STYLES[i] || { bg: 'from-slate-800/50 to-slate-800/30', border: 'border-slate-700/30', icon: 'text-slate-400', text: 'text-slate-300' };
          return (
            <div
              key={`lb-${entry.rank}`}
              className={`flex items-center justify-between bg-gradient-to-r ${style.bg} border ${style.border} rounded-xl px-4 py-2.5 transition-all hover:scale-[1.01]`}
              data-testid={`leaderboard-rank-${entry.rank}`}
            >
              <div className="flex items-center gap-3">
                <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-slate-900/60">
                  {i < 3 ? (
                    <Medal className={`w-4 h-4 ${style.icon}`} />
                  ) : (
                    <span className="text-slate-400 text-xs font-bold">#{entry.rank}</span>
                  )}
                </div>
                <span className={`text-sm font-medium ${style.text}`}>{entry.name}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <ChevronUp className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-white text-sm font-semibold">{entry.referrals}</span>
                <span className="text-slate-500 text-[10px]">referrals</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Share section for logged-in users */}
      {refCode && (
        <div className="mt-4 pt-3 border-t border-slate-700/30">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Share2 className="w-3.5 h-3.5 text-slate-400" />
              <p className="text-slate-400 text-[10px]">Invite friends & climb the ranks</p>
            </div>
            <SocialShareButtons referralLink={`${window.location.origin}?ref=${refCode}`} compact />
          </div>
        </div>
      )}
    </Card>
  );
};

export default ReferralLeaderboard;
