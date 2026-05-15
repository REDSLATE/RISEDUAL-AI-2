/**
 * HonestyMirrorCard — operator audit of the brain's own honesty
 * receipts, proxied from MC's `/api/admin/intents/honesty`.
 *
 * Shows the last 24h of intents this brain emitted, broken down by:
 *   - total intents
 *   - "blocked but directional" count (the Camaro-hold-trap signal)
 *   - top hold reasons
 *   - council penalty distribution
 *
 * `mc_status` chip surfaces whether the data is live, MC returned
 * a non-200, or MC is unreachable. The operator should NEVER be
 * left wondering whether an empty table means "no blocked trades"
 * vs "MC is down."
 *
 * Visual language matches `MemoryDriftCard.jsx` — dark slate panel,
 * cyan `#3DE8D9` accent.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ScrollText, RefreshCw, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { Card } from '../ui/card';

const API = process.env.REACT_APP_BACKEND_URL;

const STACK_OPTIONS = [
  { value: 'alpha',    label: 'Alpha 1.6' },
  { value: 'camaro',   label: 'Camaro 1.3' },
  { value: 'chevelle', label: 'Chevelle 1.3' },
  { value: 'redeye',   label: 'RedEye 1.3' },
];

const HOURS_OPTIONS = [6, 12, 24, 48, 72];

const statusBadge = (status) => {
  if (status === 'live') {
    return {
      icon: <CheckCircle2 className="w-3.5 h-3.5" />,
      text: 'MC live',
      cls: 'bg-emerald-900/30 border-emerald-700/50 text-emerald-300',
    };
  }
  if (status === 'unconfigured') {
    return {
      icon: <AlertTriangle className="w-3.5 h-3.5" />,
      text: 'MC unconfigured',
      cls: 'bg-amber-900/30 border-amber-700/50 text-amber-300',
    };
  }
  if (status === 'unreachable') {
    return {
      icon: <AlertTriangle className="w-3.5 h-3.5" />,
      text: 'MC unreachable',
      cls: 'bg-rose-900/30 border-rose-700/50 text-rose-300',
    };
  }
  return {
    icon: <AlertTriangle className="w-3.5 h-3.5" />,
    text: status || 'unknown',
    cls: 'bg-slate-800 border-slate-600 text-slate-300',
  };
};

const HonestyMirrorCard = () => {
  const [stack, setStack] = useState('alpha');
  const [hours, setHours] = useState(24);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(
        `${API}/api/sovereign/honesty-mirror?stack=${stack}&hours=${hours}`,
      );
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [stack, hours]);

  useEffect(() => {
    fetchData();
    // Auto-refresh every 90s — same cadence as a sidecar tick + a
    // bit, so the table is at most one tick out of date.
    const id = setInterval(fetchData, 90_000);
    return () => clearInterval(id);
  }, [fetchData]);

  const status = statusBadge(data?.mc_status);
  const total = Number(data?.total_intents || 0);
  const blocked = Number(data?.blocked_directional || 0);
  const blockedPct = total > 0 ? Math.round((blocked / total) * 100) : 0;
  const byReason = data?.by_reason || {};
  const reasons = Object.entries(byReason)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 5);

  return (
    <Card
      className="bg-slate-900/60 border-slate-400/25 rounded-xl p-5"
      data-testid="honesty-mirror-card"
    >
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <ScrollText className="w-4 h-4 text-[#3DE8D9]" />
        <h3 className="text-white text-sm font-semibold tracking-wide">
          Honesty Mirror
        </h3>
        <span
          className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${status.cls}`}
          data-testid="honesty-mirror-status"
        >
          {status.icon}{status.text}
        </span>

        <div className="ml-auto flex items-center gap-2">
          <select
            value={stack}
            onChange={(e) => setStack(e.target.value)}
            className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded px-2 py-1"
            data-testid="honesty-mirror-stack"
          >
            {STACK_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <select
            value={hours}
            onChange={(e) => setHours(Number(e.target.value))}
            className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded px-2 py-1"
            data-testid="honesty-mirror-hours"
          >
            {HOURS_OPTIONS.map((h) => (
              <option key={h} value={h}>{h}h</option>
            ))}
          </select>
          <button
            onClick={fetchData}
            disabled={loading}
            className="text-[#3DE8D9] hover:text-[#7AEEE0] disabled:opacity-40 p-1"
            data-testid="honesty-mirror-refresh"
            aria-label="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {error && (
        <div
          className="text-xs text-rose-300 bg-rose-900/20 border border-rose-700/40 rounded p-2 mb-3"
          data-testid="honesty-mirror-error"
        >
          fetch failed: {error}
        </div>
      )}

      {data?.note && data.mc_status !== 'live' && (
        <p className="text-xs text-slate-400 mb-3" data-testid="honesty-mirror-note">
          {data.note}
        </p>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <div className="bg-slate-800/60 border border-slate-700 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
            Intents · {hours}h
          </div>
          <div
            className="text-white text-lg font-bold tabular-nums"
            data-testid="honesty-mirror-total"
          >
            {total}
          </div>
        </div>
        <div className="bg-slate-800/60 border border-slate-700 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
            Blocked but directional
          </div>
          <div
            className={`text-lg font-bold tabular-nums ${blocked > 0 ? 'text-amber-300' : 'text-slate-300'}`}
            data-testid="honesty-mirror-blocked"
          >
            {blocked}{' '}
            <span className="text-xs text-slate-500 font-normal">
              ({blockedPct}%)
            </span>
          </div>
        </div>
        <div className="bg-slate-800/60 border border-slate-700 rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
            Top hold reasons
          </div>
          <div
            className="text-slate-300 text-xs font-semibold"
            data-testid="honesty-mirror-reasons-count"
          >
            {reasons.length === 0
              ? <span className="text-slate-500 font-normal">none</span>
              : `${reasons.length} unique`}
          </div>
        </div>
      </div>

      {reasons.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">
            Hold reason breakdown
          </div>
          <table className="w-full text-xs">
            <tbody>
              {reasons.map(([reason, n]) => {
                const pct = total > 0 ? Math.round((Number(n) / total) * 100) : 0;
                return (
                  <tr key={reason} data-testid={`honesty-mirror-reason-${reason}`}>
                    <td className="py-1 pr-3 text-slate-300">{reason}</td>
                    <td className="py-1 pr-3 text-slate-400 tabular-nums w-16 text-right">
                      {n}
                    </td>
                    <td className="py-1 w-28">
                      <div className="bg-slate-800 rounded h-1.5 overflow-hidden">
                        <div
                          className="bg-[#3DE8D9] h-full"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </td>
                    <td className="py-1 pl-3 text-slate-500 tabular-nums w-10 text-right">
                      {pct}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {total === 0 && data?.mc_status === 'live' && (
        <p className="text-xs text-slate-500 mt-3" data-testid="honesty-mirror-empty">
          No intents emitted in the last {hours}h. Either the brain
          has been quiet, or it&apos;s still on the legacy payload
          shape (no honesty receipt attached).
        </p>
      )}
    </Card>
  );
};

export default HonestyMirrorCard;
