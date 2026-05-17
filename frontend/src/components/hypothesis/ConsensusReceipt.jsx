import React from 'react';
import { Scale, AlertTriangle, ShieldOff, Zap } from 'lucide-react';
import { Card } from '../ui/card';

/**
 * ConsensusReceipt — surfaces the doctrine-required transparency
 * fields emitted by `_weighted_consensus` (see
 * `services/multi_model_hypothesis_service.py` + `confidence_weighting.py`).
 *
 * Why this exists: the prior consensus would silently collapse to
 * HOLD/0.50 on any council disagreement, hiding the fact that brains
 * had directional opinions. This panel exposes:
 *
 *   • raw vs final action (was the brain's market judgment overridden?)
 *   • raw vs final confidence (how much did the council penalty bite?)
 *   • disagreement kind (UNANIMOUS / HOLD_DISSENT / HARD_CONFLICT /
 *     ALL_HOLD)
 *   • would_have_traded_without_gates (honest market judgment, even
 *     when display action is HOLD)
 *   • per-brain dynamic weights (which brain is currently trusted more)
 *
 * Only renders when the receipt fields are present — older cached
 * results without them stay clean.
 */
const fmtKind = (k) => {
  if (!k) return '—';
  return k.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
};

const kindColor = (k) => {
  if (k === 'UNANIMOUS') return 'text-emerald-300 border-emerald-700/50 bg-emerald-900/20';
  if (k === 'HOLD_DISSENT') return 'text-amber-300 border-amber-700/50 bg-amber-900/20';
  if (k === 'HARD_CONFLICT') return 'text-rose-300 border-rose-700/50 bg-rose-900/20';
  return 'text-slate-300 border-slate-600/50 bg-slate-800/40';
};

const ConsensusReceipt = ({ hypothesis }) => {
  if (!hypothesis) return null;
  // Only render when the doctrine receipt is attached.
  const hasReceipt =
    hypothesis.raw_action !== undefined ||
    hypothesis.disagreement_kind !== undefined ||
    hypothesis.individual_weights !== undefined;
  if (!hasReceipt) return null;

  const {
    raw_action,
    raw_confidence,
    final_action,
    final_confidence,
    hold_reason,
    blocked_by,
    would_have_traded_without_gates,
    pre_weight_confidence,
    post_weight_confidence,
    council_penalty,
    disagreement_kind,
    individual_weights,
    execution_decision,
    override_reason,
    override_brain,
    override_confidence,
  } = hypothesis;

  const overridden = raw_action && final_action && raw_action !== final_action;
  const penaltyVal = Number(council_penalty || 0);

  return (
    <Card
      className="bg-slate-800/60 border-slate-400/25 rounded-xl p-5"
      data-testid="consensus-receipt"
    >
      <div className="flex items-center gap-2 mb-3">
        <Scale className="w-4 h-4 text-cyan-300" />
        <h4 className="text-white text-sm font-semibold tracking-wide">
          Decision Receipt
        </h4>
        {disagreement_kind && (
          <span
            className={`ml-auto inline-flex items-center text-[10px] px-2 py-0.5 rounded-full border ${kindColor(disagreement_kind)}`}
            data-testid="receipt-disagreement-kind"
          >
            {fmtKind(disagreement_kind)}
          </span>
        )}
        {override_reason && (
          <span
            className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border border-cyan-700/50 bg-cyan-900/20 text-cyan-300 ml-2"
            data-testid="receipt-override-badge"
            title={override_reason}
          >
            <Zap className="w-3 h-3" />
            Override: {override_brain || '—'} @ {override_confidence != null ? `${override_confidence}%` : '—'}
          </span>
        )}
      </div>

      {/* Headline row: market judgment vs display, with penalty delta */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
        <div className="bg-slate-900/50 border border-slate-700/60 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Market Judgment</div>
          <div className="text-white text-base font-bold" data-testid="receipt-raw-action">
            {raw_action || '—'}
          </div>
          <div className="text-xs text-slate-400 mt-0.5">
            {raw_confidence != null ? `${raw_confidence}% raw` : '—'}
          </div>
        </div>
        <div className="bg-slate-900/50 border border-slate-700/60 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Display Action</div>
          <div className="text-white text-base font-bold" data-testid="receipt-final-action">
            {final_action || '—'}
          </div>
          <div className="text-xs text-slate-400 mt-0.5">
            {final_confidence != null ? `${final_confidence}% final` : '—'}
          </div>
        </div>
        <div className="bg-slate-900/50 border border-slate-700/60 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Council Penalty</div>
          <div
            className={`text-base font-bold ${penaltyVal < 0 ? 'text-rose-300' : 'text-emerald-300'}`}
            data-testid="receipt-council-penalty"
          >
            {penaltyVal > 0 ? '+' : ''}{penaltyVal}%
          </div>
          <div className="text-xs text-slate-400 mt-0.5">
            {pre_weight_confidence != null && post_weight_confidence != null
              ? `${pre_weight_confidence}% → ${post_weight_confidence}%`
              : '—'}
          </div>
        </div>
      </div>

      {/* Honesty banner: brain had a directional opinion but display is HOLD */}
      {would_have_traded_without_gates && final_action === 'HOLD' && (
        <div
          className="flex items-start gap-2 bg-amber-900/20 border border-amber-700/40 rounded-lg p-3 mb-3"
          data-testid="receipt-would-have-traded"
        >
          <AlertTriangle className="w-4 h-4 text-amber-300 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-amber-200 leading-relaxed">
            The council judged this as <span className="font-bold">{raw_action}</span>, but
            execution is gated. Display action is HOLD — market judgment was not HOLD.
          </div>
        </div>
      )}

      {/* Override banner: raw → final mismatch */}
      {overridden && !would_have_traded_without_gates && (
        <div
          className="flex items-start gap-2 bg-rose-900/20 border border-rose-700/40 rounded-lg p-3 mb-3"
          data-testid="receipt-overridden"
        >
          <ShieldOff className="w-4 h-4 text-rose-300 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-rose-200 leading-relaxed">
            Raw <span className="font-bold">{raw_action}</span> was overridden to <span className="font-bold">{final_action}</span>
            {hold_reason ? ` — ${fmtKind(hold_reason)}` : ''}.
          </div>
        </div>
      )}

      {/* Footer: gate list + execution doctrine + per-brain weights */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1.5">Gates</div>
          <div className="flex flex-wrap gap-1.5">
            {(blocked_by || []).length === 0 ? (
              <span className="text-xs text-slate-500">none</span>
            ) : (
              blocked_by.map((g) => (
                <span
                  key={g}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-slate-900 border border-slate-700 text-slate-300"
                  data-testid={`receipt-gate-${g}`}
                >
                  {g}
                </span>
              ))
            )}
            {execution_decision && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-900 border border-slate-700 text-cyan-300 ml-auto">
                exec: {execution_decision}
              </span>
            )}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1.5">Brain Weights</div>
          {individual_weights && Object.keys(individual_weights).length > 0 ? (
            <div className="grid grid-cols-2 gap-1">
              {Object.entries(individual_weights).map(([brain, w]) => (
                <div
                  key={brain}
                  className="flex items-center justify-between text-[11px] text-slate-300"
                  data-testid={`receipt-brain-weight-${brain}`}
                >
                  <span className="capitalize">{brain}</span>
                  <span className={Number(w) > 1.05 ? 'text-emerald-300' : Number(w) < 0.95 ? 'text-rose-300' : 'text-slate-400'}>
                    {Number(w).toFixed(2)}×
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <span className="text-xs text-slate-500">balanced (1.00×)</span>
          )}
        </div>
      </div>
    </Card>
  );
};

export default ConsensusReceipt;
