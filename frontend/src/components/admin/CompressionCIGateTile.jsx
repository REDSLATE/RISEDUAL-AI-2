import React, { useState, useCallback } from 'react';
import { ShieldCheck, AlertTriangle, HelpCircle, Loader2 } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Compression CI Gate tile — preview a candidate model's regime-aware
 * calibration drift vs a baseline before promoting a quantized /
 * pruned variant.
 *
 * Read-only — calls /api/admin/compression-ci-gate which runs the
 * same `evaluate_gate` pure function used by the Makefile + pytest
 * entry points. No writes, no sizing changes, no IP risk.
 *
 * Owner-only endpoint; the chip surfaces a 403 cleanly.
 */
const VERDICT_CONFIG = {
  PASS: {
    label: 'PASS',
    Icon: ShieldCheck,
    border: 'border-emerald-500/40',
    bg: 'bg-emerald-500/10',
    text: 'text-emerald-300',
    badge: 'bg-emerald-500/20 text-emerald-200',
  },
  FAIL: {
    label: 'FAIL',
    Icon: AlertTriangle,
    border: 'border-rose-500/50',
    bg: 'bg-rose-500/10',
    text: 'text-rose-300',
    badge: 'bg-rose-500/20 text-rose-200',
  },
  INCONCLUSIVE: {
    label: 'INCONCLUSIVE',
    Icon: HelpCircle,
    border: 'border-amber-500/40',
    bg: 'bg-amber-500/10',
    text: 'text-amber-300',
    badge: 'bg-amber-500/20 text-amber-200',
  },
};

const CompressionCIGateTile = () => {
  const [baselineTag, setBaselineTag] = useState('');
  const [candidateTag, setCandidateTag] = useState('');
  const [windowDays, setWindowDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const evaluate = useCallback(async () => {
    if (!baselineTag.trim() || !candidateTag.trim()) {
      setError('Both baseline and candidate model_version values are required.');
      return;
    }
    if (baselineTag.trim() === candidateTag.trim()) {
      setError('Baseline and candidate must differ.');
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const params = new URLSearchParams({
        baseline_tag: baselineTag.trim(),
        candidate_tag: candidateTag.trim(),
        window_days: String(windowDays),
      });
      const resp = await authFetch(
        `${API}/admin/compression-ci-gate?${params.toString()}`,
      );
      if (!resp.ok) {
        const msg = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${msg.slice(0, 200)}`);
      }
      setResult(await resp.json());
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [baselineTag, candidateTag, windowDays]);

  const verdictCfg = result ? VERDICT_CONFIG[result.verdict] : null;

  return (
    <div
      className="rounded-lg border border-slate-700/50 bg-slate-900/40 p-4 space-y-3"
      data-testid="compression-ci-gate-tile"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-cyan-400" />
          <h3 className="text-sm font-semibold text-slate-200">
            Compression CI Gate
          </h3>
        </div>
        <span className="text-xs text-slate-500">read-only · owner</span>
      </div>

      <p className="text-xs text-slate-400 leading-relaxed">
        Compare a candidate model's regime-aware calibration vs a baseline
        before promoting a quantized / pruned variant. Both tags must
        match values stamped on{' '}
        <code className="text-cyan-300">predictions.model_version</code>.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <input
          type="text"
          value={baselineTag}
          onChange={(e) => setBaselineTag(e.target.value)}
          placeholder="baseline tag (e.g. v0.1.0)"
          data-testid="compression-gate-baseline-input"
          className="bg-slate-950/60 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500/60"
        />
        <input
          type="text"
          value={candidateTag}
          onChange={(e) => setCandidateTag(e.target.value)}
          placeholder="candidate tag (e.g. v0.1.0-quantized)"
          data-testid="compression-gate-candidate-input"
          className="bg-slate-950/60 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500/60"
        />
      </div>

      <div className="flex items-center gap-2">
        <label className="text-xs text-slate-400">window:</label>
        <select
          value={windowDays}
          onChange={(e) => setWindowDays(Number(e.target.value))}
          data-testid="compression-gate-window-select"
          className="bg-slate-950/60 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200"
        >
          <option value={7}>7 days</option>
          <option value={14}>14 days</option>
          <option value={30}>30 days</option>
          <option value={60}>60 days</option>
          <option value={90}>90 days</option>
        </select>
        <button
          type="button"
          onClick={evaluate}
          disabled={loading}
          data-testid="compression-gate-evaluate-button"
          className="ml-auto bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-200 text-xs font-medium px-3 py-1.5 rounded border border-cyan-500/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
        >
          {loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {loading ? 'Evaluating…' : 'Evaluate'}
        </button>
      </div>

      {error && (
        <div
          className="rounded border border-rose-500/30 bg-rose-500/10 p-2 text-xs text-rose-300"
          data-testid="compression-gate-error"
        >
          {error}
        </div>
      )}

      {result && verdictCfg && (
        <div
          className={`rounded border ${verdictCfg.border} ${verdictCfg.bg} p-3 space-y-2`}
          data-testid="compression-gate-result"
        >
          <div className="flex items-center gap-2">
            <verdictCfg.Icon className={`w-4 h-4 ${verdictCfg.text}`} />
            <span className={`text-xs font-bold tracking-wide ${verdictCfg.text}`}>
              {verdictCfg.label}
            </span>
            <span className="text-xs text-slate-400 ml-auto">
              window: {result.window_days}d
            </span>
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <div className="text-slate-500 uppercase tracking-wide text-[10px] mb-0.5">
                Baseline
              </div>
              <div className="text-slate-300 font-mono">
                {result.baseline_tag}
              </div>
              <div className="text-slate-400">
                samples: {result.baseline_summary?.samples ?? '—'}
              </div>
              <div className="text-slate-400">
                weighted gap:{' '}
                {result.baseline_summary?.weighted_avg_calibration_gap != null
                  ? result.baseline_summary.weighted_avg_calibration_gap.toFixed(4)
                  : '—'}
              </div>
            </div>
            <div>
              <div className="text-slate-500 uppercase tracking-wide text-[10px] mb-0.5">
                Candidate
              </div>
              <div className="text-slate-300 font-mono">
                {result.candidate_tag}
              </div>
              <div className="text-slate-400">
                samples: {result.candidate_summary?.samples ?? '—'}
              </div>
              <div className="text-slate-400">
                weighted gap:{' '}
                {result.candidate_summary?.weighted_avg_calibration_gap != null
                  ? result.candidate_summary.weighted_avg_calibration_gap.toFixed(4)
                  : '—'}
              </div>
            </div>
          </div>

          {result.breaches?.length > 0 && (
            <div
              className="border-t border-slate-700/50 pt-2 space-y-1"
              data-testid="compression-gate-breaches"
            >
              <div className="text-[10px] uppercase tracking-wide text-slate-500">
                {result.inconclusive ? 'Reasons' : 'Breaches'}
              </div>
              {result.breaches.map((b, i) => (
                <div
                  key={i}
                  className="text-xs text-slate-300 font-mono leading-snug"
                >
                  · {b}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default CompressionCIGateTile;
