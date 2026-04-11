import React, { useState, useEffect } from 'react';
import { Target } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

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

  const acc24 = stats.accuracy_24h;
  const acc1w = stats.accuracy_1w;
  const displayAcc = acc1w !== null ? acc1w : acc24;
  const timeframe = acc1w !== null ? '1W' : '24H';
  const total = acc1w !== null ? stats.total_1w : stats.total_24h;

  if (displayAcc === null) {
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

  const color = displayAcc >= 60 ? 'text-lime-400 border-lime-700/40 bg-green-600'
    : displayAcc >= 50 ? 'text-amber-300 border-amber-800/40 bg-amber-900/20'
    : 'text-orange-400 border-orange-700/40 bg-orange-900';

  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${color} ${className}`} data-testid={`accuracy-badge-${feature}`} title={`Based on ${total} verified predictions (${timeframe})`}>
      <Target className="w-2.5 h-2.5" />
      {displayAcc.toFixed(1)}% ({timeframe})
    </span>
  );
};

export default AccuracyBadge;
