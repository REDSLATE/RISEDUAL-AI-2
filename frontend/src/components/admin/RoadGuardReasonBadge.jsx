/**
 * RoadGuardReasonBadge primitives:
 *   - <DecisionBadge> ALLOW / BLOCK / PAUSE inline label
 *   - <ChecklistRow>  pass/fail row with icon + detail used in the
 *                     promotion checklist block.
 */
import { AlertTriangle, CheckCircle2 } from 'lucide-react';

export const DecisionBadge = ({ value }) => {
  if (value === 'ALLOW') {
    return <span className="text-emerald-300">ALLOW</span>;
  }
  if (value === 'BLOCK') {
    return <span className="text-rose-300">BLOCK</span>;
  }
  if (value === 'PAUSE_LANE') {
    return <span className="text-amber-300">PAUSE</span>;
  }
  return <span className="text-slate-400">{value || '—'}</span>;
};

export const ChecklistRow = ({ label, pass, detail }) => (
  <div className="flex items-center justify-between text-xs">
    <span className="flex items-center gap-2 text-slate-300">
      {pass ? (
        <CheckCircle2 className="w-3 h-3 text-emerald-400" />
      ) : (
        <AlertTriangle className="w-3 h-3 text-amber-400" />
      )}
      {label}
    </span>
    <span className="font-mono text-slate-400">{detail}</span>
  </div>
);

export default DecisionBadge;
