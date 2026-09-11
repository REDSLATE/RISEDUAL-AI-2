import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Radio, ShieldCheck, Zap, CircleDollarSign, Activity,
  RefreshCw, PlayCircle, Settings2, AlertTriangle, CheckCircle2, XCircle,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const HEALTH_API      = `${getApiBase()}/api/admin/alpha-daytrader/moomoo-bridge/health`;
const SMOKE_API       = `${getApiBase()}/api/admin/alpha-daytrader/moomoo-bridge/smoke-test`;
const ENDPOINT_API    = `${getApiBase()}/api/admin/alpha-daytrader/moomoo-bridge/endpoint`;

/**
 * ConnectMoomooCard — local OpenD tunnel + readiness ladder.
 *
 * Ladder rungs (monotonic):
 *   DISCONNECTED → CONNECTED → DATA_READY → RESEARCH_READY → EXECUTION_READY.
 *
 * Public.com is fully independent — MooMoo's ladder never blocks Public's
 * trading path. The Funnel gains MooMoo as a second research witness at
 * RESEARCH_READY. Execution routes via MooMoo only at EXECUTION_READY.
 *
 * Smoke test is READ-ONLY: tunnel → OpenD → account → fresh quote →
 * normalized BrokerResearchSnapshot. No orders submitted.
 */
const LADDER = [
  { key: 'DISCONNECTED',    label: 'Disconnected',    icon: XCircle,      cls: 'bg-slate-500/10 border-slate-500/40 text-slate-300' },
  { key: 'CONNECTED',       label: 'OpenD Reachable', icon: Radio,        cls: 'bg-sky-500/10 border-sky-500/40 text-sky-300' },
  { key: 'DATA_READY',      label: 'Market Data',     icon: Activity,     cls: 'bg-cyan-500/10 border-cyan-500/40 text-cyan-300' },
  { key: 'RESEARCH_READY',  label: 'Research Witness',icon: ShieldCheck,  cls: 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300' },
  { key: 'EXECUTION_READY', label: 'Execution Armed', icon: Zap,          cls: 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300' },
];

const rungOrder = (state) => Math.max(0, LADDER.findIndex((r) => r.key === state));

const StatusPill = ({ ok, label, hint }) => (
  <div
    data-testid={`moomoo-pill-${label.toLowerCase().replace(/\s+/g, '-')}`}
    className={`flex items-center gap-2 rounded-md border px-2 py-1 text-xs ${
      ok ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300'
         : 'bg-zinc-500/10 border-zinc-500/40 text-zinc-400'}`}
  >
    {ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
    <span className="font-medium">{label}</span>
    {hint ? <span className="text-[10px] text-zinc-500 truncate">{hint}</span> : null}
  </div>
);

const ConnectMoomooCard = () => {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [smokeRunning, setSmokeRunning] = useState(false);
  const [smoke, setSmoke] = useState(null);
  const [showConfig, setShowConfig] = useState(false);
  const [cfg, setCfg] = useState({ host: '', port: '', canary_symbol: '', execution_ready: false });
  const [cfgError, setCfgError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await authFetch(HEALTH_API);
      if (r.ok) {
        const j = await r.json();
        setHealth(j);
        setCfg((c) => ({
          host:            c.host || j?.opend?.host || '',
          port:            c.port || String(j?.opend?.port || ''),
          canary_symbol:   c.canary_symbol || j?.market_data?.canary_symbol || '',
          execution_ready: !!j?.gates?.execution_ready_flag,
        }));
      }
    } catch (_e) {
      // Silent — card just stays in loading spinner briefly.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const currentRung = rungOrder(health?.state);
  const stateMeta   = useMemo(
    () => LADDER.find((r) => r.key === health?.state) || LADDER[0],
    [health?.state],
  );
  const StateIcon = stateMeta.icon;

  const runSmokeTest = useCallback(async () => {
    setSmokeRunning(true);
    setSmoke(null);
    try {
      const r = await authFetch(SMOKE_API, { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      setSmoke(j);
    } catch (e) {
      setSmoke({ overall: { ok: false, failed_at: 'network', error: e.message } });
    } finally {
      setSmokeRunning(false);
      load();
    }
  }, [load]);

  const saveEndpoint = useCallback(async () => {
    setCfgError(null);
    try {
      const body = {};
      if (cfg.host) body.host = cfg.host;
      if (cfg.port) body.port = parseInt(cfg.port, 10);
      if (cfg.canary_symbol) body.canary_symbol = cfg.canary_symbol;
      body.execution_ready = !!cfg.execution_ready;
      const r = await authFetch(ENDPOINT_API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      await load();
      setShowConfig(false);
    } catch (e) {
      setCfgError(e.message || String(e));
    }
  }, [cfg, load]);

  return (
    <div
      data-testid="moomoo-bridge-card"
      className="rounded-xl border border-zinc-800/80 bg-zinc-950/60 p-4 space-y-4"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <CircleDollarSign size={18} className="text-indigo-300" />
          <div>
            <h3 className="text-sm font-semibold text-zinc-100">MooMoo Bridge</h3>
            <p className="text-[11px] text-zinc-500">Local OpenD · Public.com runs independently</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            data-testid="moomoo-refresh-btn"
            className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition"
            onClick={load}
            title="Refresh health"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            data-testid="moomoo-config-btn"
            className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition"
            onClick={() => setShowConfig((s) => !s)}
            title="Endpoint config"
          >
            <Settings2 size={14} />
          </button>
        </div>
      </div>

      {/* State banner */}
      <div
        data-testid="moomoo-state-banner"
        className={`flex items-center justify-between rounded-lg border px-3 py-2 ${stateMeta.cls}`}
      >
        <div className="flex items-center gap-2">
          <StateIcon size={16} />
          <div className="text-sm font-semibold">{stateMeta.label}</div>
        </div>
        <div className="text-[10px] uppercase tracking-wider opacity-70">
          {health?.state || 'unknown'}
        </div>
      </div>

      {/* Ladder */}
      <div className="grid grid-cols-5 gap-1">
        {LADDER.slice(1).map((rung, idx) => {
          const active = currentRung >= idx + 1;
          const RungIcon = rung.icon;
          return (
            <div
              key={rung.key}
              data-testid={`moomoo-rung-${rung.key.toLowerCase()}`}
              className={`flex flex-col items-center gap-1 rounded-md border px-1.5 py-2 text-[10px] transition ${
                active ? rung.cls : 'bg-zinc-900/40 border-zinc-800 text-zinc-600'
              }`}
              title={rung.key}
            >
              <RungIcon size={14} />
              <div className="text-center leading-tight">{rung.label}</div>
            </div>
          );
        })}
      </div>

      {/* Diagnostics */}
      <div className="flex flex-wrap gap-2">
        <StatusPill
          ok={!!health?.opend?.reachable}
          label="OpenD"
          hint={health?.opend?.tcp_latency_ms != null ? `${health.opend.tcp_latency_ms}ms` : ''}
        />
        <StatusPill
          ok={!!health?.market_data?.ok}
          label="Quote"
          hint={
            health?.market_data?.quote_latency_ms != null
              ? `${health.market_data.canary_symbol || ''} ${health.market_data.quote_latency_ms}ms`
              : ''
          }
        />
        <StatusPill
          ok={!!health?.trade?.account_ok}
          label="Account"
          hint={
            health?.trade?.buying_power_usd != null
              ? `$${Number(health.trade.buying_power_usd).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
              : ''
          }
        />
        <StatusPill
          ok={!!health?.gates?.live_enabled}
          label="Live Flag"
          hint={health?.gates?.live_enabled ? 'ON' : 'OFF'}
        />
        <StatusPill
          ok={!!health?.gates?.execution_ready_flag}
          label="Exec Ready"
          hint={health?.gates?.execution_ready_flag ? 'ON' : 'OFF'}
        />
      </div>

      {/* Warnings */}
      {(health?.warnings?.length || health?.errors?.length) ? (
        <div
          data-testid="moomoo-warnings"
          className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300"
        >
          <div className="flex items-center gap-1 font-medium">
            <AlertTriangle size={12} /> Diagnostics
          </div>
          <ul className="mt-1 space-y-0.5 list-disc pl-4">
            {(health?.errors || []).map((e, i) => (<li key={`e-${i}`} className="text-red-300">{e}</li>))}
            {(health?.warnings || []).map((w, i) => (<li key={`w-${i}`}>{w}</li>))}
          </ul>
        </div>
      ) : null}

      {/* Smoke test */}
      <div className="flex items-center justify-between rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2">
        <div>
          <div className="text-xs font-semibold text-zinc-200">Read-only Smoke Test</div>
          <div className="text-[10px] text-zinc-500">Tunnel → OpenD → account → quote → research snapshot · never submits</div>
        </div>
        <button
          data-testid="moomoo-smoke-test-btn"
          onClick={runSmokeTest}
          disabled={smokeRunning}
          className="flex items-center gap-1.5 rounded-md border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/20 disabled:opacity-50 transition"
        >
          <PlayCircle size={14} className={smokeRunning ? 'animate-pulse' : ''} />
          {smokeRunning ? 'Running…' : 'Run'}
        </button>
      </div>

      {smoke ? (
        <div
          data-testid="moomoo-smoke-result"
          className={`rounded-md border px-3 py-2 text-xs space-y-1 ${
            smoke?.overall?.ok
              ? 'border-emerald-500/40 bg-emerald-500/5 text-emerald-200'
              : 'border-red-500/40 bg-red-500/5 text-red-200'
          }`}
        >
          <div className="flex items-center gap-2 font-semibold">
            {smoke?.overall?.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
            {smoke?.overall?.ok
              ? `PASS · ${smoke.overall.duration_ms || 0}ms`
              : `FAIL at ${smoke?.overall?.failed_at || 'unknown'}`}
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
            <div>Tunnel: <span className={smoke?.tunnel?.ok ? 'text-emerald-300' : 'text-red-300'}>{smoke?.tunnel?.ok ? 'OK' : 'FAIL'}</span></div>
            <div>Market data: <span className={smoke?.market_data?.ok ? 'text-emerald-300' : 'text-red-300'}>{smoke?.market_data?.ok ? 'OK' : 'FAIL'}</span></div>
            <div>Account: <span className={smoke?.account?.ok ? 'text-emerald-300' : 'text-red-300'}>{smoke?.account?.ok ? 'OK' : 'FAIL'}</span></div>
            <div>Research: <span className={smoke?.research_snapshot?.ok ? 'text-emerald-300' : 'text-red-300'}>{smoke?.research_snapshot?.ok ? 'OK' : 'FAIL'}</span></div>
          </div>
          {smoke?.research_snapshot?.ok && smoke?.research_snapshot?.current_price ? (
            <div className="text-[11px] text-zinc-400">
              Broker={smoke.research_snapshot.broker} · Price=${smoke.research_snapshot.current_price} · Spread={smoke.research_snapshot.spread_bps ?? '—'}bps · Age={smoke.research_snapshot.broker_quote_age_seconds ?? '—'}s
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Config drawer */}
      {showConfig ? (
        <div
          data-testid="moomoo-config-drawer"
          className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-3 space-y-2 text-xs"
        >
          <div className="text-zinc-300 font-medium">Tunnel endpoint</div>
          <p className="text-[11px] text-zinc-500 leading-snug">
            Point at your local OpenD (via ngrok / Cloudflare Tunnel / Tailscale). Runtime override — persists in this
            process only. Add to <code>backend/.env</code> for cross-restart persistence.
          </p>
          <div className="grid grid-cols-3 gap-2">
            <input
              data-testid="moomoo-cfg-host"
              className="col-span-2 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-zinc-200 focus:border-indigo-500 outline-none"
              placeholder="host (e.g. tunnel.example.com)"
              value={cfg.host}
              onChange={(e) => setCfg((c) => ({ ...c, host: e.target.value }))}
            />
            <input
              data-testid="moomoo-cfg-port"
              className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-zinc-200 focus:border-indigo-500 outline-none"
              placeholder="port"
              value={cfg.port}
              onChange={(e) => setCfg((c) => ({ ...c, port: e.target.value }))}
            />
            <input
              data-testid="moomoo-cfg-canary"
              className="col-span-3 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-zinc-200 focus:border-indigo-500 outline-none"
              placeholder="canary symbol (default SPY)"
              value={cfg.canary_symbol}
              onChange={(e) => setCfg((c) => ({ ...c, canary_symbol: e.target.value.toUpperCase() }))}
            />
          </div>
          <label
            data-testid="moomoo-cfg-exec-ready"
            className="flex items-center gap-2 text-zinc-300 pt-1 cursor-pointer"
          >
            <input
              type="checkbox"
              checked={cfg.execution_ready}
              onChange={(e) => setCfg((c) => ({ ...c, execution_ready: e.target.checked }))}
            />
            <span>Enable execution routing (requires MOOMOO_LIVE_ENABLED in .env too)</span>
          </label>
          {cfgError ? (
            <div data-testid="moomoo-cfg-error" className="text-red-300">{cfgError}</div>
          ) : null}
          <div className="flex items-center justify-end gap-2">
            <button
              data-testid="moomoo-cfg-cancel"
              className="rounded-md border border-zinc-700 px-3 py-1 text-zinc-400 hover:bg-zinc-800"
              onClick={() => setShowConfig(false)}
            >
              Cancel
            </button>
            <button
              data-testid="moomoo-cfg-save"
              className="rounded-md border border-indigo-500/40 bg-indigo-500/10 px-3 py-1 text-indigo-200 hover:bg-indigo-500/20"
              onClick={saveEndpoint}
            >
              Save
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default ConnectMoomooCard;
