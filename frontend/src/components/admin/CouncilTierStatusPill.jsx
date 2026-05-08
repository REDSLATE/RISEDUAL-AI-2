import React, { useEffect, useState, useCallback } from 'react';

/**
 * Council Tier Status Pill — peripheral-vision indicator showing
 * how close Council is to flip-ready.
 *
 * Polls /api/admin/shadow/tier-readiness every 30s and renders a
 * tiny status chip with one of four states:
 *
 *   - "Council: ready to flip" (emerald) — all 3 gates passed,
 *     env flag still false → operator action required
 *   - "Council: live" (cyan) — all gates passed AND env flag true,
 *     modulator is actually shaping risk
 *   - "Council: 2/3 gates" / "1/3 gates" / "0/3 gates" (slate) —
 *     progress indicator while gates are pending
 *   - hidden when fetch errors (silent failure — pill is a "nice
 *     to know" not a "must know")
 *
 * Click → opens the Shadow tab so the operator can drill into
 * which specific bucket needs more dissents. Click handler is
 * passed in by AdminPanel so the pill stays decoupled from
 * navigation logic.
 *
 * Polling cadence (30s) matches the rest of the admin telemetry
 * surface — the network load is one tiny GET per pill instance.
 */
export default function CouncilTierStatusPill({ apiBase, onOpenShadowTab }) {
  const [data, setData] = useState(null);
  const [errored, setErrored] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/admin/shadow/tier-readiness`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setErrored(false);
    } catch {
      // Silent — pill hides on error rather than nagging the operator.
      setErrored(true);
    }
  }, [apiBase]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  if (errored || !data) {
    return null;
  }

  // Compute the 3-gate progress count.
  const tier3 = !!data.tier3_unlocked;
  const phaseFull = data.adversarial_phase === 'full';
  const anyBucket = (data.council_buckets || []).some((b) => b.open);
  const gatesPassed = [tier3, phaseFull, anyBucket].filter(Boolean).length;

  let label;
  let tone;

  if (data.council_modulator_enabled && data.ready_to_enable_council) {
    label = 'Council: live';
    tone = 'border-cyan-500/40 bg-cyan-950/40 text-cyan-300';
  } else if (data.ready_to_enable_council) {
    label = 'Council: ready to flip';
    tone = 'border-emerald-500/40 bg-emerald-950/40 text-emerald-300';
  } else {
    label = `Council: ${gatesPassed}/3 gates`;
    tone = 'border-slate-600 bg-slate-900 text-slate-400';
  }

  return (
    <button
      type="button"
      onClick={onOpenShadowTab}
      className={`px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-semibold border transition-colors hover:brightness-125 ${tone}`}
      title="Click to open the Shadow tab and see per-bucket details"
      data-testid="council-tier-status-pill"
    >
      {label}
    </button>
  );
}
