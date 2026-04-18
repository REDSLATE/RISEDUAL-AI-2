import React from 'react';
import { Sparkles, Swords } from 'lucide-react';
import { openWarRoomForTicker } from '../../utils/deepLink';

/**
 * Compact "Smart Money Score shifted" alerts stack. Each row is two buttons
 * side-by-side:
 *   - Left button opens the AI chat pre-filled with a regime-change prompt.
 *   - Right pill routes to the AI War Room with the ticker auto-analyzed.
 */
const SmartMoneyShiftAlerts = ({ shifts }) => {
  if (!shifts?.length) return null;
  return (
    <div className="mb-3 space-y-1.5" data-testid="watchlist-sms-shifts">
      {shifts.map((shift) => {
        const up = shift.delta > 0;
        const topMover = shift.top_movers?.[0];
        const onAsk = () => {
          const prompt = `${shift.symbol} Smart Money Score shifted from ${shift.prev_score} to ${shift.new_score} (${shift.delta > 0 ? '+' : ''}${shift.delta} pts) — now ${shift.signal}. ${topMover ? `Notable move: ${topMover.institution} ${topMover.type} its position.` : ''} What's driving this regime change? Actionable take?`;
          window.dispatchEvent(new CustomEvent('risedualai-open-chat', { detail: { prefill: prompt, autoSend: true } }));
        };
        return (
          <div key={`${shift.symbol}-${shift.date}`} className="flex items-stretch gap-1">
            <button
              onClick={onAsk}
              className={`flex-1 flex items-center gap-2 text-left px-3 py-2 rounded-lg border text-xs transition-colors hover:brightness-110 ${
                up ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-red-500/10 border-red-500/30'
              }`}
              data-testid={`watchlist-sms-shift-${shift.symbol}`}
            >
              <Sparkles className={`w-3.5 h-3.5 flex-shrink-0 ${up ? 'text-emerald-400' : 'text-red-400'}`} />
              <span className="text-white font-bold">{shift.symbol}</span>
              <span className="text-slate-300">Smart Money</span>
              <span className={`font-mono font-bold ${up ? 'text-emerald-400' : 'text-red-400'}`}>
                {shift.prev_score} {up ? '↗' : '↘'} {shift.new_score}
              </span>
              <span className={`text-[10px] ${up ? 'text-emerald-400' : 'text-red-400'}`}>
                ({shift.delta > 0 ? '+' : ''}{shift.delta} pts)
              </span>
              {shift.signal_change && (
                <span className="text-slate-400 text-[10px] truncate">now {shift.signal}</span>
              )}
            </button>
            <button
              onClick={() => openWarRoomForTicker({ ticker: shift.symbol, source: 'SM Shift Alert' })}
              className={`shrink-0 inline-flex items-center gap-1 px-2 rounded-lg border text-[10px] font-bold uppercase tracking-wider transition-colors hover:brightness-125 ${
                up ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300' : 'bg-red-500/15 border-red-500/40 text-red-300'
              }`}
              title={`Open ${shift.symbol} in AI War Room`}
              data-testid={`watchlist-sms-shift-warroom-${shift.symbol}`}
            >
              <Swords className="w-3 h-3" />
              <span className="hidden sm:inline">War Room</span>
              <span>→</span>
            </button>
          </div>
        );
      })}
    </div>
  );
};

export default SmartMoneyShiftAlerts;
