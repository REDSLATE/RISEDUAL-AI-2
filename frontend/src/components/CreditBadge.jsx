import React, { useState, useEffect, useCallback } from 'react';
import { Coins } from 'lucide-react';
import { authFetch, useAuth } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const CreditBadge = ({ onClick }) => {
  const { user } = useAuth();
  const [credits, setCredits] = useState(null);

  const fetchBalance = useCallback(async () => {
    if (!user) return;
    try {
      const res = await authFetch(`${API}/credits/balance`);
      if (res.ok) {
        const d = await res.json();
        setCredits(d.credits || 0);
      }
    } catch {
      // silent
    }
  }, [user]);

  useEffect(() => {
    fetchBalance();
    const interval = setInterval(fetchBalance, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, [fetchBalance]);

  if (!user || credits === null) return null;

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 bg-slate-800/80 hover:bg-slate-700/80 border border-amber-500/20 rounded-lg px-2.5 py-1.5 transition-all group"
      title="AI Credits"
      data-testid="credit-badge"
    >
      <Coins className="w-3.5 h-3.5 text-amber-400 group-hover:scale-110 transition-transform" />
      <span className="text-amber-400 text-xs font-bold">{credits.toLocaleString()}</span>
    </button>
  );
};

export default CreditBadge;
