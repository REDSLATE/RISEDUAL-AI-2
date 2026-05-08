/**
 * RoadGuardLaneCard — per-lane summary card used in the lane header
 * row of RoadGuardTile. Click toggles the parent's slice filter.
 *
 * Read-only — never mutates RoadGuard state.
 */
const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString());

export const RoadGuardLaneCard = ({
  label, bucket, active, onClick, testid, dotColor, dim,
}) => {
  const total = bucket?.total || 0;
  const blocks = bucket?.decision_counts?.BLOCK || 0;
  const pauses = bucket?.decision_counts?.PAUSE_LANE || 0;
  const blockRate = total ? (blocks + pauses) / total : 0;
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      disabled={dim || !total}
      className={[
        'rounded-lg border p-2 text-left transition-colors',
        active
          ? 'border-emerald-500/60 bg-emerald-500/10'
          : 'border-slate-700/40 bg-slate-800/30 hover:bg-slate-800/60',
        dim ? 'opacity-50 cursor-default' : 'cursor-pointer',
        !total && !dim ? 'opacity-60' : '',
      ].join(' ')}
    >
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
        <span className="text-[10px] uppercase tracking-wide text-slate-400">
          {label}
        </span>
      </div>
      <div className="mt-1 flex items-baseline justify-between">
        <span className="text-base font-mono text-slate-100">
          {fmtNum(total)}
        </span>
        <span className="text-[10px] font-mono text-slate-500">
          {(blockRate * 100).toFixed(0)}% block
        </span>
      </div>
      {total > 0 && (
        <div className="text-[10px] font-mono text-slate-500">
          A {bucket?.decision_counts?.ALLOW ?? 0} · B {blocks} · P {pauses}
        </div>
      )}
    </button>
  );
};

export default RoadGuardLaneCard;
