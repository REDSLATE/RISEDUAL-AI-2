import React, { useState, useEffect } from 'react';
import { Crown, Sparkles, Shield, Zap, Trophy, Users, Calendar, X } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';

const API = getApiBase();

const BADGE_CONFIG = {
  creator: { label: 'Creator', icon: Crown, gradient: 'from-amber-500/20 to-orange-500/20', border: 'border-amber-500/30', text: 'text-amber-300' },
  founding: { label: 'Founding 100', icon: Sparkles, gradient: 'from-violet-500/20 to-purple-500/20', border: 'border-violet-500/30', text: 'text-violet-300' },
  beta: { label: 'Beta Tester', icon: Zap, gradient: 'from-[#3DE8D9]/15 to-teal-500/10', border: 'border-[#3DE8D9]/30', text: 'text-[#3DE8D9]' },
  pro: { label: 'Pro Member', icon: Shield, gradient: 'from-[#3DE8D9]/15 to-cyan-500/10', border: 'border-[#3DE8D9]/30', text: 'text-[#3DE8D9]' },
  free: { label: 'Member', icon: Users, gradient: 'from-slate-700/50 to-slate-800/50', border: 'border-slate-600/30', text: 'text-slate-400' },
};

const PublicProfile = ({ userId, onClose }) => {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!userId) return;
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/referral/profile/${userId}`);
        if (res.ok) setProfile(await res.json());
      } catch (e) {
        logger.warn('Profile load error:', e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [userId]);

  if (!userId) return null;

  const badge = BADGE_CONFIG[profile?.badge] || BADGE_CONFIG.free;
  const BadgeIcon = badge.icon;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[100] flex items-center justify-center p-4" onClick={onClose} data-testid="public-profile-modal">
      <div className="bg-[#0B1426] border border-slate-500/25 rounded-2xl w-full max-w-sm overflow-hidden shadow-2xl" onClick={e => e.stopPropagation()}>
        {loading ? (
          <div className="p-8 space-y-4 animate-pulse">
            <div className="w-16 h-16 bg-slate-700/50 rounded-full mx-auto" />
            <div className="h-4 bg-slate-700/50 rounded w-1/2 mx-auto" />
            <div className="h-3 bg-slate-700/50 rounded w-1/3 mx-auto" />
          </div>
        ) : !profile ? (
          <div className="p-8 text-center">
            <p className="text-slate-400 text-sm">Profile not found</p>
            <button onClick={onClose} className="mt-3 text-[#3DE8D9] text-xs hover:underline">Close</button>
          </div>
        ) : (
          <>
            {/* Header with gradient background */}
            <div className={`relative bg-gradient-to-br ${badge.gradient} px-6 pt-8 pb-6`}>
              <button onClick={onClose} className="absolute top-3 right-3 text-slate-400 hover:text-white transition-colors" data-testid="profile-close">
                <X className="w-4 h-4" />
              </button>

              {/* Avatar */}
              <div className="w-16 h-16 rounded-full bg-slate-800/80 border-2 border-slate-500/40 flex items-center justify-center mx-auto mb-3">
                <span className="text-2xl font-bold text-white">
                  {(profile.name || '?')[0].toUpperCase()}
                </span>
              </div>

              {/* Name */}
              <h2 className="text-white text-lg font-bold text-center" data-testid="profile-name">{profile.name}</h2>

              {/* Badge */}
              <div className="flex justify-center mt-2">
                <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-gradient-to-r ${badge.gradient} border ${badge.border} text-xs font-bold ${badge.text}`} data-testid="profile-badge">
                  <BadgeIcon className="w-3.5 h-3.5" />
                  {badge.label}
                </span>
              </div>
            </div>

            {/* Stats */}
            <div className="px-6 py-5">
              <div className="grid grid-cols-3 gap-3 mb-5">
                <StatBox
                  icon={<Trophy className="w-4 h-4 text-amber-300" />}
                  value={profile.leaderboard_rank ? `#${profile.leaderboard_rank}` : '--'}
                  label="Rank"
                  testId="profile-rank"
                />
                <StatBox
                  icon={<Users className="w-4 h-4 text-[#3DE8D9]" />}
                  value={profile.total_referrals || 0}
                  label="Referrals"
                  testId="profile-referrals"
                />
                <StatBox
                  icon={<Calendar className="w-4 h-4 text-violet-400" />}
                  value={profile.member_since || '--'}
                  label="Joined"
                  testId="profile-joined"
                />
              </div>

              {/* Badge collection */}
              <div className="mb-4">
                <p className="text-slate-400 text-[10px] font-medium uppercase tracking-wider mb-2">Badges Earned</p>
                <div className="flex flex-wrap gap-2" data-testid="profile-badges-list">
                  {profile.role === 'owner' || profile.role === 'admin' ? (
                    <BadgePill type="creator" />
                  ) : null}
                  {profile.founding_member && <BadgePill type="founding" />}
                  {profile.beta_access && <BadgePill type="beta" />}
                  {profile.subscription_status === 'pro' && <BadgePill type="pro" />}
                  {/* Everyone gets the member badge */}
                  <BadgePill type="free" />
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

const StatBox = ({ icon, value, label, testId }) => (
  <div className="bg-slate-800/50 rounded-xl p-3 text-center" data-testid={testId}>
    <div className="flex justify-center mb-1">{icon}</div>
    <p className="text-white text-sm font-bold">{value}</p>
    <p className="text-slate-400 text-[9px]">{label}</p>
  </div>
);

const BadgePill = ({ type }) => {
  const cfg = BADGE_CONFIG[type] || BADGE_CONFIG.free;
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full bg-gradient-to-r ${cfg.gradient} border ${cfg.border} text-[10px] font-bold ${cfg.text}`}>
      <Icon className="w-3 h-3" />
      {cfg.label}
    </span>
  );
};

export default PublicProfile;
export { BADGE_CONFIG };
