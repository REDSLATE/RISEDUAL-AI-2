/**
 * ML Health Strip — three compact admin cards:
 *
 *   1. **Tier 3 readiness** — composite 0-100 score + failing-reason
 *      list from the 6-gate `/api/admin/tier3-readiness` endpoint.
 *      Replaces the simple "days / 30" progress bar with something
 *      richer: exposure, high-conf accuracy, calibration gap, risk
 *      control, stability, canary.
 *   2. **Clamp canary** — score-boundary hit-count from
 *      `/api/admin/conviction/clamp-canary`.
 *   3. **Calibration (ECE)** — Expected Calibration Error from
 *      `/api/admin/conviction/reliability` (decile reliability
 *      diagram).
 *
 * All three endpoints fail closed server-side so a network wobble
 * renders "loading" / neutral cards rather than a false alarm.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Gauge,
  Rocket,
  Scale,
  TrendingUp,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api`;

const Card = ({ icon: Icon, title, children, testId, tone = 'default' }) => {
  const accent =
    tone === 'ok'
      ? 'border-emerald-400/30 bg-emerald-900/10'
      : tone === 'warn'
      ? 'border-amber-400/40 bg-amber-900/15'
      : 'border-slate-400/20 bg-slate-800/50';
  return (
    <div className={`p-3 rounded-lg border ${accent}`} data-testid={testId}>
      <div className="flex items-center gap-2 mb-2">
        <Icon className="w-3.5 h-3.5 text-[#3DE8D9]" />
        <span className="text-[10px] uppercase tracking-wide text-slate-300">
          {title}
        </span>
      </div>
      {children}
    </div>
  );
};

const Tier3ReadinessCard = ({ data }) => {
  if (!data) {
    return (
      <Card icon={Rocket} title="Tier 3 readiness" testId="ml-health-tier3">
        <div className="text-xs text-slate-400">Loading…</div>
      </Card>
    );
  }
  const stats = data.stats || {};
  const unlock = data.unlock || {};
  const score = Math.max(0, Math.min(100, unlock.confidence_score ?? 0));
  const unlocked = unlock.unlocked === true;
  const barColor = unlocked ? 'bg-emerald-400' : 'bg-[#3DE8D9]';
  const reasons = Array.isArray(unlock.reasons) ? unlock.reasons : [];
  return (
    <Card
      icon={Rocket}
      title="Tier 3 live-unlock readiness"
      testId="ml-health-tier3"
      tone={unlocked ? 'ok' : 'default'}
    >
      <div className="flex items-baseline justify-between">
        <div className="text-xl font-bold text-white tabular-nums">
          {score.toFixed(1)}
          <span className="text-sm text-slate-400 font-normal"> / 100</span>
        </div>
        <span className="text-[10px] text-slate-400 tabular-nums">
          {stats.days ?? 0}d · {stats.total_trades ?? 0} trades
        </span>
      </div>
      <div className="h-1.5 bg-slate-900/60 rounded-full overflow-hidden mt-2">
        <div
          className={`h-full ${barColor} transition-all`}
          style={{ width: `${score}%` }}
        />
      </div>
      <div className="mt-2 text-[10px]">
        {unlocked ? (
          <span className="text-emerald-300">
            all 6 gates clear — live execution approved
          </span>
        ) : reasons.length === 0 ? (
          <span className="text-slate-400">evaluating…</span>
        ) : (
          <ul
            className="text-amber-300 list-disc pl-3.5 space-y-0.5"
            data-testid="tier3-blocking-reasons"
          >
            {reasons.slice(0, 3).map((r) => (
              <li key={r}>{r}</li>
            ))}
            {reasons.length > 3 && (
              <li className="text-slate-500">
                +{reasons.length - 3} more
              </li>
            )}
          </ul>
        )}
      </div>
    </Card>
  );
};

const ClampCanaryCard = ({ data }) => {
  if (!data) {
    return (
      <Card icon={Gauge} title="Clamp canary" testId="ml-health-clamp">
        <div className="text-xs text-slate-400">Loading…</div>
      </Card>
    );
  }
  const healthy = data.status === 'ok';
  const Icon = healthy ? CheckCircle2 : AlertTriangle;
  const iconColor = healthy ? 'text-emerald-400' : 'text-amber-400';
  return (
    <Card
      icon={Gauge}
      title="Conviction clamp canary"
      testId="ml-health-clamp"
      tone={healthy ? 'ok' : 'warn'}
    >
      <div className="flex items-baseline justify-between">
        <div className="text-xl font-bold text-white tabular-nums">
          {data.clamp_total}
          <span className="text-sm text-slate-400 font-normal">
            {' '}/ {data.total_graded}
          </span>
        </div>
        <span className={`flex items-center gap-1 text-[10px] ${iconColor}`}>
          <Icon className="w-3 h-3" />
          {healthy ? 'ok' : 'warn'}
        </span>
      </div>
      <div className="mt-2 text-[10px] text-slate-400">
        scores at ±{data.min_reward?.toFixed(1) ?? '2.5'} in last{' '}
        {data.lookback_days}d
      </div>
      <div className="mt-1 text-[10px] text-slate-500">
        {data.clamp_rate_pct?.toFixed(2) ?? '0.00'}% clamp rate ·{' '}
        <span className="text-slate-400">
          hi {data.clamp_high} · lo {data.clamp_low}
        </span>
      </div>
    </Card>
  );
};

const ReliabilityCard = ({ data }) => {
  if (!data) {
    return (
      <Card icon={Scale} title="Calibration (ECE)" testId="ml-health-ece">
        <div className="text-xs text-slate-400">Loading…</div>
      </Card>
    );
  }
  const ece = data.ece;
  const hasData = ece !== null && ece !== undefined;
  const healthy = data.well_calibrated === true;
  const Icon = healthy ? CheckCircle2 : AlertTriangle;
  const iconColor = healthy ? 'text-emerald-400' : 'text-amber-400';

  // Dominant gap sign lets admins see UNDER vs OVER-confidence at a
  // glance. We pick the most-populated bucket with data and read its
  // sign — cheap approximation that matches the reliability diagram's
  // visual intuition.
  let dominantGap = null;
  if (Array.isArray(data.buckets)) {
    const populated = data.buckets
      .filter((b) => typeof b.gap === 'number' && b.total > 0)
      .sort((a, b) => b.total - a.total);
    if (populated[0]) dominantGap = populated[0].gap;
  }
  const direction =
    dominantGap == null
      ? null
      : dominantGap > 0.05
      ? 'under-confident'
      : dominantGap < -0.05
      ? 'over-confident'
      : 'calibrated';
  return (
    <Card
      icon={Scale}
      title="Calibration · ECE"
      testId="ml-health-ece"
      tone={healthy ? 'ok' : 'warn'}
    >
      <div className="flex items-baseline justify-between">
        <div className="text-xl font-bold text-white tabular-nums">
          {hasData ? ece.toFixed(3) : '—'}
          <span className="text-sm text-slate-400 font-normal"> ECE</span>
        </div>
        <span className={`flex items-center gap-1 text-[10px] ${iconColor}`}>
          <Icon className="w-3 h-3" />
          {healthy ? 'ok' : 'warn'}
        </span>
      </div>
      <div className="mt-2 text-[10px] text-slate-400">
        {data.total_verified ?? 0} verified ·{' '}
        {data.overall_accuracy != null
          ? `${(data.overall_accuracy * 100).toFixed(1)}% overall`
          : 'no data'}
      </div>
      {direction && (
        <div className="mt-1 text-[10px] text-slate-500">
          trend: <span className="text-slate-300">{direction}</span>
          {dominantGap != null && (
            <span className="tabular-nums">
              {' '}
              ({dominantGap >= 0 ? '+' : ''}
              {dominantGap.toFixed(2)})
            </span>
          )}
        </div>
      )}
    </Card>
  );
};

const RDistributionCard = ({ data }) => {
  if (!data) {
    return (
      <Card icon={TrendingUp} title="R-distribution" testId="ml-health-r-dist">
        <div className="text-xs text-slate-400">Loading…</div>
      </Card>
    );
  }
  const rDist = data.r_distribution || {};
  const meanR = typeof rDist.mean_r === 'number' ? rDist.mean_r : null;
  const strongFrac =
    typeof rDist.strong_r_frac === 'number' ? rDist.strong_r_frac : null;
  const resolved = data.total_resolved ?? 0;
  const hasSignal = resolved > 0 && meanR !== null;

  // Tone: strong negative mean_r is a regime-shift warning.
  // >= 0: healthy book; 0 to -0.25: neutral/flat; < -0.25: warn.
  const tone = !hasSignal
    ? 'default'
    : meanR >= 0
    ? 'ok'
    : meanR < -0.25
    ? 'warn'
    : 'default';
  const barColor =
    tone === 'ok'
      ? 'bg-emerald-400'
      : tone === 'warn'
      ? 'bg-amber-400'
      : 'bg-[#3DE8D9]';
  // Map mean_r in [-2, +2] to 0-100% fill; 50% = break-even.
  const fillPct = hasSignal
    ? Math.max(0, Math.min(100, ((meanR + 2) / 4) * 100))
    : 0;
  const Icon = hasSignal && tone === 'warn' ? AlertTriangle : TrendingUp;
  const iconColor = tone === 'warn' ? 'text-amber-400' : 'text-emerald-400';

  return (
    <Card
      icon={TrendingUp}
      title="R-distribution · trade book"
      testId="ml-health-r-dist"
      tone={tone}
    >
      <div className="flex items-baseline justify-between">
        <div
          className="text-xl font-bold text-white tabular-nums"
          data-testid="r-dist-mean"
        >
          {hasSignal ? (meanR >= 0 ? '+' : '') + meanR.toFixed(2) : '—'}
          <span className="text-sm text-slate-400 font-normal"> mean R</span>
        </div>
        <span className={`flex items-center gap-1 text-[10px] ${iconColor}`}>
          <Icon className="w-3 h-3" />
          {!hasSignal
            ? 'no data'
            : tone === 'ok'
            ? 'healthy'
            : tone === 'warn'
            ? 'drift'
            : 'flat'}
        </span>
      </div>
      <div className="h-1.5 bg-slate-900/60 rounded-full overflow-hidden mt-2">
        <div
          className={`h-full ${barColor} transition-all`}
          style={{ width: `${fillPct}%` }}
          data-testid="r-dist-mean-bar"
        />
      </div>
      <div className="mt-2 text-[10px] text-slate-400">
        {resolved} resolved ·{' '}
        {strongFrac !== null ? (
          <span className="tabular-nums" data-testid="r-dist-strong-frac">
            {(strongFrac * 100).toFixed(0)}% strong (|R| ≥ 1.5)
          </span>
        ) : (
          '—'
        )}
      </div>
      <div className="mt-1 text-[10px] text-slate-500">
        {!hasSignal
          ? 'waiting on resolved trades'
          : tone === 'warn'
          ? 'sustained negative mean — regime shift risk'
          : tone === 'ok'
          ? 'book expectancy positive'
          : 'near break-even'}
      </div>
    </Card>
  );
};


const PaperDaysProgressCard = ({ data }) => {
  if (!data) {
    return (
      <Card icon={CalendarClock} title="Paper days → Tier 3" testId="ml-health-paper-days">
        <div className="text-xs text-slate-400">Loading…</div>
      </Card>
    );
  }
  const days = data.days ?? 0;
  const target = data.target_days ?? 30;
  const remaining = data.remaining_days ?? Math.max(target - days, 0);
  const pct = Math.max(0, Math.min(100, data.progress_pct ?? Math.round((days / target) * 100)));
  const unlocked = data.unlocked === true;
  const total = data.total_trades ?? 0;
  const override = data.override_env;

  return (
    <Card
      icon={CalendarClock}
      title="Paper days → Tier 3"
      testId="ml-health-paper-days"
      tone={unlocked ? 'ok' : 'default'}
    >
      <div className="flex items-baseline justify-between">
        <div className="text-xl font-bold text-white tabular-nums">
          {days}
          <span className="text-sm text-slate-400 font-normal"> / {target} days</span>
        </div>
        <span className="text-[10px] text-slate-400 tabular-nums">
          {total} trades
        </span>
      </div>
      <div className="h-1.5 bg-slate-900/60 rounded-full overflow-hidden mt-2">
        <div
          className={`h-full ${unlocked ? 'bg-emerald-400' : 'bg-[#3DE8D9]'} transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-2 text-[10px]">
        {unlocked ? (
          <span className="text-emerald-300">30-day floor cleared — Tier 3 gate open.</span>
        ) : (
          <span className="text-slate-400">
            {remaining} day{remaining === 1 ? '' : 's'} remaining before the ML pipeline can enter Tier 3.
          </span>
        )}
        {override && (
          <span className="ml-1 text-amber-300">
            · override: RISEDUAL_LIVE_DAYS={override}
          </span>
        )}
      </div>
    </Card>
  );
};


const MLHealthStrip = () => {
  const [tier3, setTier3] = useState(null);
  const [progress, setProgress] = useState(null);
  const [canary, setCanary] = useState(null);
  const [reliability, setReliability] = useState(null);
  const [leSummary, setLeSummary] = useState(null);

  const load = useCallback(async () => {
    try {
      const [t3, pr, c, r, le] = await Promise.all([
        authFetch(`${API}/admin/tier3-readiness?days=30`),
        authFetch(`${API}/admin/tier3-progress`),
        authFetch(`${API}/admin/conviction/clamp-canary?days=30`),
        authFetch(`${API}/admin/conviction/reliability?days=30`),
        authFetch(`${API}/admin/learning-engine/summary`),
      ]);
      if (t3.ok) setTier3(await t3.json());
      if (pr.ok) setProgress(await pr.json());
      if (c.ok) setCanary(await c.json());
      if (r.ok) setReliability(await r.json());
      if (le.ok) setLeSummary(await le.json());
    } catch (e) {
      logger.error('ml health strip load failed', e);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div
      className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3"
      data-testid="ml-health-strip"
    >
      <Tier3ReadinessCard data={tier3} />
      <PaperDaysProgressCard data={progress} />
      <RDistributionCard data={leSummary} />
      <ClampCanaryCard data={canary} />
      <ReliabilityCard data={reliability} />
    </div>
  );
};

export default MLHealthStrip;
