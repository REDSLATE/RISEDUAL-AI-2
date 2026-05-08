/**
 * RoadGuardStatsRow primitives:
 *   - <Metric>      single accent-coloured counter cell
 *   - <ScopeButton> aggregate / equity / crypto scope toggle pill
 *
 * Pure presentational — no state, no API calls.
 */

export const Metric = ({ label, value, sub, accent, testid }) => {
  const accentClass =
    accent === 'emerald'
      ? 'text-emerald-300'
      : accent === 'rose'
        ? 'text-rose-300'
        : accent === 'amber'
          ? 'text-amber-300'
          : 'text-slate-100';
  return (
    <div
      className="rounded-lg border border-slate-700/40 bg-slate-800/30 p-2"
      data-testid={testid}
    >
      <div className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`text-base font-mono ${accentClass}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 font-mono">{sub}</div>}
    </div>
  );
};

export const ScopeButton = ({ label, active, onClick, testid, disabled }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    data-testid={testid}
    className={[
      'px-2 py-0.5 rounded font-mono transition-colors',
      active
        ? 'bg-emerald-500/20 text-emerald-200 border border-emerald-500/40'
        : 'text-slate-400 border border-transparent hover:bg-slate-800/60',
      disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
    ].join(' ')}
  >
    {label}
  </button>
);

export default Metric;
