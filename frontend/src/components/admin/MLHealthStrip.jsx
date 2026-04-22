/**
 * Compact "ML Health Strip" shown at the top of the Conviction admin tab.
 *
 * Two signals, side by side:
 *   1. Tier 3 paper-trading progress — distinct UTC days with ML-orchestrator
 *      auto-trades, on the path to the 30-day unlock gate.
 *   2. Conviction clamp canary — count of prediction outcomes hitting the
 *      ±2.5 score boundary in the last 30 days. Zero today; any non-zero
 *      count means `GRADE_WEIGHTS` drifted past the clamp.
 *
 * Both endpoints fail closed server-side (zero counts on any error), so
 * a network wobble renders the "no data" state rather than a false alarm.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Gauge, Rocket } from 'lucide-react';
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
    <div
      className={`p-3 rounded-lg border ${accent}`}
      data-testid={testId}
    >
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

const Tier3Card = ({ data }) => {
  if (!data) {
    return (
      <Card icon={Rocket} title="Tier 3 progress" testId="ml-health-tier3">
        <div className="text-xs text-slate-400">Loading…</div>
      </Card>
    );
  }
  const pct = Math.max(0, Math.min(100, data.progress_pct || 0));
  const barColor = data.unlocked ? 'bg-emerald-400' : 'bg-[#3DE8D9]';
  return (
    <Card
      icon={Rocket}
      title="Tier 3 paper-trading gate"
      testId="ml-health-tier3"
      tone={data.unlocked ? 'ok' : 'default'}
    >
      <div className="flex items-baseline justify-between">
        <div className="text-xl font-bold text-white tabular-nums">
          {data.days}
          <span className="text-sm text-slate-400 font-normal">
            {' '}/ {data.target_days} days
          </span>
        </div>
        {data.override_env && (
          <span
            className="text-[9px] uppercase text-amber-400"
            title={`RISEDUAL_LIVE_DAYS=${data.override_env} overrides DB count`}
          >
            env override
          </span>
        )}
      </div>
      <div className="h-1.5 bg-slate-900/60 rounded-full overflow-hidden mt-2">
        <div
          className={`h-full ${barColor} transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center justify-between mt-2 text-[10px] text-slate-400">
        <span>
          {data.unlocked
            ? 'unlocked — live execution gate clear'
            : `${data.remaining_days} days to unlock`}
        </span>
        <span className="tabular-nums">{data.total_trades} trades</span>
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

const MLHealthStrip = () => {
  const [tier3, setTier3] = useState(null);
  const [canary, setCanary] = useState(null);

  const load = useCallback(async () => {
    try {
      const [t3, c] = await Promise.all([
        authFetch(`${API}/admin/tier3-progress`),
        authFetch(`${API}/admin/conviction/clamp-canary?days=30`),
      ]);
      if (t3.ok) setTier3(await t3.json());
      if (c.ok) setCanary(await c.json());
    } catch (e) {
      logger.error('ml health strip load failed', e);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div
      className="grid grid-cols-1 sm:grid-cols-2 gap-3"
      data-testid="ml-health-strip"
    >
      <Tier3Card data={tier3} />
      <ClampCanaryCard data={canary} />
    </div>
  );
};

export default MLHealthStrip;
