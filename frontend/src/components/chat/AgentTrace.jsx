import React from 'react';

/**
 * AgentTrace — live display of tool calls while the streaming agent runs.
 *
 * Each trace entry is either a "calling" (spinner + label) or "done" (checkmark +
 * result). Results can include price, future_value, cagr_pct, historical_cagr_pct
 * which are rendered inline as monospaced numbers.
 */
export default function AgentTrace({ trace }) {
  if (!trace || trace.length === 0) return null;
  return (
    <div
      className="px-3 py-2 border-b border-slate-700/40 bg-slate-900/60 flex-shrink-0"
      data-testid="agent-trace"
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <div className="w-1.5 h-1.5 rounded-full bg-[#3DE8D9] animate-pulse" />
        <span className="text-[10px] text-[#3DE8D9] font-semibold uppercase tracking-wider">
          Agent Working
        </span>
      </div>
      <div className="space-y-1">
        {trace.map((t, i) => (
          <div key={`${t.type}-${t.label}-${i}`} className="flex items-center gap-2 text-[11px]">
            {t.type === 'calling' ? (
              <>
                <div className="w-3 h-3 border border-[#3DE8D9]/40 border-t-[#3DE8D9] rounded-full animate-spin" />
                <span className="text-slate-400">{t.label}...</span>
              </>
            ) : (
              <>
                <svg className="w-3 h-3 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                <span className="text-slate-300">{t.label}</span>
                {t.result?.price && <span className="text-[#3DE8D9] font-mono">${t.result.price}</span>}
                {t.result?.future_value && <span className="text-[#3DE8D9] font-mono">${t.result.future_value.toLocaleString()}</span>}
                {t.result?.cagr_pct && <span className="text-[#3DE8D9] font-mono">{t.result.cagr_pct}%</span>}
                {t.result?.historical_cagr_pct && <span className="text-[#3DE8D9] font-mono">{t.result.historical_cagr_pct}% CAGR</span>}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
