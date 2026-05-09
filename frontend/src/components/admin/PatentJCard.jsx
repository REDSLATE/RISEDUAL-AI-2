import React from 'react';
import { ShieldAlert, RefreshCw, Activity, AlertTriangle, ChevronUp } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * PatentJCard — Chevelle calibration reliability surface.
 *
 * Surfaces the post-hoc isotonic calibration layer's health to the
 * operator: ECE, Brier, sample count, active artifact version, the
 * reliability bin table (predicted vs. realised win rate per
 * decile), and a manual "Refit now" button.
 *
 * Hard rule reflected in copy: this is observation only. The
 * calibrator does NOT lower Patent J thresholds, does NOT change
 * execution authority, does NOT emit verdicts.
 */
function MetricTile({ label, value, accent = 'cyan', testid }) {
  const accentMap = {
    cyan:    'text-cyan-400',
    emerald: 'text-emerald-400',
    amber:   'text-amber-400',
    rose:    'text-rose-400',
    slate:   'text-slate-300',
  };
  return (
    <div
      data-testid={testid}
      className="bg-slate-900/60 border border-slate-800 rounded-lg p-4"
    >
      <div className="text-[11px] uppercase tracking-widest text-slate-500 mb-1">
        {label}
      </div>
      <div className={`text-2xl font-semibold ${accentMap[accent]}`}>
        {value}
      </div>
    </div>
  );
}

function ReliabilityBars({ bins }) {
  if (!bins || bins.length === 0) {
    return (
      <div className="text-xs text-slate-500 italic py-6 text-center">
        No bins yet — fit the calibrator first.
      </div>
    );
  }
  const max = Math.max(...bins.map((b) => b.count), 1);
  return (
    <div className="space-y-1.5" data-testid="patent-j-reliability-bars">
      {bins.map((b, idx) => {
        const drift = b.fraction_positive - b.mean_predicted;
        const driftClass = Math.abs(drift) < 0.05
          ? 'text-emerald-400'
          : Math.abs(drift) < 0.15
            ? 'text-amber-400'
            : 'text-rose-400';
        return (
          <div key={idx} className="flex items-center gap-3 text-xs font-mono">
            <span className="w-20 text-slate-500">
              {b.bin_lo.toFixed(2)}–{b.bin_hi.toFixed(2)}
            </span>
            <div className="flex-1 h-5 bg-slate-900 rounded overflow-hidden flex items-center">
              <div
                className="h-full bg-cyan-500/30 border-r border-cyan-400"
                style={{ width: `${(b.count / max) * 100}%` }}
              />
            </div>
            <span className="w-12 text-right text-slate-300">{b.count}</span>
            <span className="w-16 text-right text-slate-300">
              {b.mean_predicted.toFixed(3)}
            </span>
            <span className="w-16 text-right text-slate-300">
              {b.fraction_positive.toFixed(3)}
            </span>
            <span className={`w-16 text-right ${driftClass}`}>
              {drift >= 0 ? '+' : ''}{drift.toFixed(3)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default function PatentJCard() {
  const [status, setStatus] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [refitting, setRefitting] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [refitResult, setRefitResult] = React.useState(null);

  const fetchStatus = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await authFetch(`${API}/governance/chevelle/calibration/status`);
      if (!r.ok) throw new Error(`status failed (${r.status})`);
      setStatus(await r.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const onRefit = async () => {
    setRefitting(true);
    setRefitResult(null);
    setError(null);
    try {
      const r = await authFetch(
        `${API}/governance/chevelle/calibration/refit`,
        { method: 'POST' },
      );
      const json = await r.json();
      if (!r.ok) {
        throw new Error(json.detail || `refit failed (${r.status})`);
      }
      setRefitResult(json);
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    } finally {
      setRefitting(false);
    }
  };

  const health = status?.apply_health || {};
  const stale = health.stale === true;
  const loaded = health.calibrator_loaded === true;

  return (
    <div data-testid="patent-j-card" className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-100 mb-1 flex items-center gap-2">
            <ShieldAlert className="text-cyan-400" size={22} />
            Patent J — Calibration Reliability
          </h2>
          <p className="text-sm text-slate-400 max-w-2xl">
            Post-hoc isotonic mapping of raw confidence → calibrated confidence.
            Fit on firewall-trainable rows only. Quarantined / unresolved /
            non-binary outcomes are filtered.
            <span className="text-amber-400">
              {' '}Observation only — does not lower Patent J thresholds, does
              not change execution authority.
            </span>
          </p>
        </div>
        <button
          type="button"
          data-testid="patent-j-refit-btn"
          disabled={refitting}
          onClick={onRefit}
          className={`px-4 py-2 rounded-md text-sm font-semibold transition-colors flex items-center gap-2 ${
            refitting
              ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
              : 'bg-cyan-500 hover:bg-cyan-400 text-slate-950'
          }`}
        >
          <RefreshCw size={14} className={refitting ? 'animate-spin' : ''} />
          {refitting ? 'Refitting…' : 'Refit now'}
        </button>
      </div>

      {error && (
        <div className="bg-rose-500/10 border border-rose-500/30 rounded-lg px-4 py-3 text-sm text-rose-300 flex items-start gap-2"
             data-testid="patent-j-error">
          <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
          <div>{error}</div>
        </div>
      )}

      {refitResult && (
        <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4 text-sm" data-testid="patent-j-refit-result">
          <div className="text-xs uppercase tracking-widest text-slate-500 mb-2">
            Last refit
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-slate-300">
            <div>
              <span className="text-slate-500">success</span>{' '}
              <span className={refitResult.success ? 'text-emerald-400' : 'text-rose-400'}>
                {String(refitResult.success)}
              </span>
            </div>
            <div><span className="text-slate-500">samples</span> {refitResult.sample_count}</div>
            <div><span className="text-slate-500">version</span> <span className="font-mono text-xs">{refitResult.model_version || '—'}</span></div>
            <div><span className="text-slate-500">rows pulled</span> {refitResult.rows_pulled}</div>
            {refitResult.rejected_reason && (
              <div className="col-span-full text-amber-400 text-xs">{refitResult.rejected_reason}</div>
            )}
          </div>
        </div>
      )}

      {loading && !status && (
        <div className="text-xs text-slate-500 italic py-6 text-center" data-testid="patent-j-loading">
          Loading reliability snapshot…
        </div>
      )}

      {status && (
        <>
          {/* Health pills */}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className={`px-2.5 py-1 rounded-full font-mono uppercase tracking-wider border ${
              loaded ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                     : 'bg-rose-500/10 text-rose-400 border-rose-500/30'
            }`} data-testid="patent-j-pill-loaded">
              {loaded ? 'Calibrator loaded' : 'No active calibrator'}
            </span>
            {loaded && (
              <span className={`px-2.5 py-1 rounded-full font-mono uppercase tracking-wider border ${
                stale ? 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                      : 'bg-slate-800 text-slate-300 border-slate-700'
              }`} data-testid="patent-j-pill-stale">
                {stale ? 'Stale' : 'Fresh'}
              </span>
            )}
            {status.active_version && (
              <span className="px-2.5 py-1 rounded-full font-mono text-slate-400 bg-slate-800/60 border border-slate-700" data-testid="patent-j-pill-version">
                v {status.active_version}
              </span>
            )}
            <span className="text-slate-500 ml-2">
              min samples: {health.min_samples_required} · stale after {health.stale_after_hours}h
            </span>
          </div>

          {/* Headline metrics */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <MetricTile
              testid="patent-j-metric-samples"
              label="Samples"
              value={status.sample_count}
              accent="cyan"
            />
            <MetricTile
              testid="patent-j-metric-total-seen"
              label="Rows seen"
              value={status.total_seen}
              accent="slate"
            />
            <MetricTile
              testid="patent-j-metric-ece"
              label="ECE"
              value={(status.ece ?? 0).toFixed(4)}
              accent={status.ece > 0.1 ? 'amber' : 'emerald'}
            />
            <MetricTile
              testid="patent-j-metric-brier"
              label="Brier"
              value={(status.brier ?? 0).toFixed(4)}
              accent={status.brier > 0.25 ? 'amber' : 'emerald'}
            />
          </div>

          {/* Reliability bin table */}
          <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-3">
              <Activity size={14} />
              Reliability bins
              <ChevronUp size={12} className="text-slate-600 ml-2" />
              <span className="text-slate-600 normal-case tracking-normal">
                drift = realised − predicted
              </span>
            </div>
            <div className="flex items-center gap-3 text-[10px] uppercase tracking-widest text-slate-600 font-mono mb-2 px-1">
              <span className="w-20">range</span>
              <span className="flex-1">distribution</span>
              <span className="w-12 text-right">n</span>
              <span className="w-16 text-right">predicted</span>
              <span className="w-16 text-right">realised</span>
              <span className="w-16 text-right">drift</span>
            </div>
            <ReliabilityBars bins={status.bins || []} />
          </div>

          <div className="text-xs text-slate-600 pt-2 border-t border-slate-800/40">
            Daily refit runs at 04:15 UTC. Manual refits + reliability snapshot are owner-only.
            Fall-back behaviour: if the calibrator is missing, stale, or fails, served confidence
            mirrors raw confidence and <code className="text-slate-400">calibration_applied=false</code>.
          </div>
        </>
      )}
    </div>
  );
}
