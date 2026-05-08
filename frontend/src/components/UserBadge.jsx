import React from 'react';
import { Crown, Sparkles, Shield, Zap } from 'lucide-react';

/**
 * UserBadge — displays the user's role/status badge.
 * 
 * Priority: Creator (owner/admin) > Founding 100 > Beta > Pro > Free
 */
const UserBadge = ({ user, size = 'sm' }) => {
  if (!user) return null;

  const isSmall = size === 'sm';
  const iconSize = isSmall ? 'w-3 h-3' : 'w-3.5 h-3.5';
  const textSize = isSmall ? 'text-[10px]' : 'text-xs';
  const padding = isSmall ? 'px-1.5 py-0.5' : 'px-2 py-1';

  // Creator badge (Admin / Owner)
  if (user.role === 'owner') {
    return (
      <span className={`inline-flex items-center gap-1 ${padding} rounded-full bg-gradient-to-r from-amber-500/20 to-orange-500/20 border border-amber-500/30 ${textSize} font-bold text-amber-300`}
        data-testid="badge-creator" title="Creator — Managing Director">
        <Crown className={iconSize} /> Creator
      </span>
    );
  }

  if (user.role === 'admin') {
    return (
      <span className={`inline-flex items-center gap-1 ${padding} rounded-full bg-gradient-to-r from-orange-500/20 to-red-500/20 border border-orange-500/30 ${textSize} font-bold text-orange-300`}
        data-testid="badge-creator" title="Creator — Admin">
        <Shield className={iconSize} /> Creator
      </span>
    );
  }

  // Founding 100 member
  if (user.founding_member) {
    return (
      <span className={`inline-flex items-center gap-1 ${padding} rounded-full bg-gradient-to-r from-violet-500/20 to-purple-500/20 border border-violet-500/30 ${textSize} font-bold text-violet-300`}
        data-testid="badge-founding" title="Founding 100 Member">
        <Sparkles className={iconSize} /> Founding 100
      </span>
    );
  }

  // Beta tester
  if (user.beta_access) {
    return (
      <span className={`inline-flex items-center gap-1 ${padding} rounded-full bg-[#3DE8D9]/15 border border-[#3DE8D9]/30 ${textSize} font-bold text-[#3DE8D9]`}
        data-testid="badge-beta" title="Beta Tester">
        <Zap className={iconSize} /> Beta
      </span>
    );
  }

  // Pro / Free (existing behavior, kept as fallback)
  const isPro = user.subscription_status === 'pro';
  return (
    <span className={`${textSize} font-bold uppercase ${padding} rounded ${isPro ? 'bg-[#3DE8D9]/20 text-[#3DE8D9]' : 'bg-slate-700 text-slate-400'}`}
      data-testid={isPro ? 'badge-pro' : 'badge-free'}>
      {isPro ? 'PRO' : 'FREE'}
    </span>
  );
};

export default UserBadge;
