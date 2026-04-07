import React, { useState, useEffect } from 'react';
import { X, Gift, Clock, ChevronRight, Zap } from 'lucide-react';
import { useAuth, authFetch } from '../contexts/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;

const PromoBanner = ({ onSubscribe }) => {
  const [promo, setPromo] = useState(null);
  const [progress, setProgress] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const [timeLeft, setTimeLeft] = useState('');
  const { user } = useAuth();

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/promo/active`);
        if (res.ok) {
          const data = await res.json();
          if (data.promo) setPromo(data.promo);
        }
      } catch (e) { console.error('Promo load error:', e); }
    };
    load();
  }, []);

  useEffect(() => {
    if (!user || !promo) return;
    const loadProgress = async () => {
      try {
        const res = await authFetch(`${API}/api/promo/progress`);
        if (res.ok) setProgress(await res.json());
      } catch (e) { console.error('Promo progress error:', e); }
    };
    loadProgress();
  }, [user, promo]);

  // Countdown timer
  useEffect(() => {
    if (!promo) return;
    const update = () => {
      const end = new Date(promo.end_date);
      const now = new Date();
      const diff = end - now;
      if (diff <= 0) { setTimeLeft('Expired'); return; }
      const d = Math.floor(diff / 86400000);
      const h = Math.floor((diff % 86400000) / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      if (d > 0) setTimeLeft(`${d}d ${h}h left`);
      else if (h > 0) setTimeLeft(`${h}h ${m}m left`);
      else setTimeLeft(`${m}m left`);
    };
    update();
    const interval = setInterval(update, 60000);
    return () => clearInterval(interval);
  }, [promo]);

  // Check localStorage for dismissal
  useEffect(() => {
    if (promo) {
      const key = `promo_dismissed_${promo.id}`;
      if (localStorage.getItem(key) === 'true') setDismissed(true);
    }
  }, [promo]);

  if (!promo || dismissed || timeLeft === 'Expired') return null;

  const dismiss = () => {
    localStorage.setItem(`promo_dismissed_${promo.id}`, 'true');
    setDismissed(true);
  };

  const progressCount = progress?.progress || 0;
  const target = progress?.target || promo.referral_target;
  const completed = progress?.completed || false;
  const pct = Math.min(100, Math.round((progressCount / target) * 100));

  return (
    <div className="sticky top-0 z-40 w-full" data-testid="promo-banner">
      <div className="bg-gradient-to-r from-[#0052FF] via-indigo-600 to-purple-600 px-4 py-2.5 relative overflow-hidden">
        {/* Animated background shimmer */}
        <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent animate-shimmer" />

        <div className="max-w-[1600px] mx-auto flex items-center justify-between gap-3 relative">
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-white/15 flex items-center justify-center flex-shrink-0">
              <Gift className="w-4 h-4 text-white" />
            </div>

            <div className="flex items-center gap-3 flex-1 min-w-0 flex-wrap">
              <div className="flex items-center gap-2">
                <Zap className="w-3.5 h-3.5 text-yellow-300 flex-shrink-0" />
                <p className="text-white text-sm font-semibold truncate" data-testid="promo-title">{promo.title}</p>
              </div>
              <p className="text-white/80 text-xs hidden sm:block">{promo.message}</p>
            </div>
          </div>

          <div className="flex items-center gap-3 flex-shrink-0">
            {/* Progress bar (logged-in users) */}
            {user && (
              <div className="hidden md:flex items-center gap-2">
                <div className="w-24 h-1.5 bg-white/20 rounded-full overflow-hidden">
                  <div className="h-full bg-yellow-300 rounded-full transition-all duration-500" style={{ width: `${pct}%` }} />
                </div>
                <span className="text-white/80 text-[10px] font-medium whitespace-nowrap" data-testid="promo-progress">
                  {completed ? 'Claimed!' : `${progressCount}/${target}`}
                </span>
              </div>
            )}

            {/* Timer */}
            <div className="flex items-center gap-1 bg-white/10 rounded-lg px-2 py-1" data-testid="promo-timer">
              <Clock className="w-3 h-3 text-white/70" />
              <span className="text-white text-[10px] font-medium whitespace-nowrap">{timeLeft}</span>
            </div>

            {/* CTA */}
            {!user && (
              <button
                onClick={onSubscribe}
                className="flex items-center gap-1 bg-white text-[#0052FF] text-xs font-semibold px-3 py-1.5 rounded-lg hover:bg-white/90 transition-all"
                data-testid="promo-cta"
              >
                Get Started <ChevronRight className="w-3 h-3" />
              </button>
            )}

            {/* Dismiss */}
            <button onClick={dismiss} className="text-white/50 hover:text-white p-0.5" data-testid="promo-dismiss">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PromoBanner;
