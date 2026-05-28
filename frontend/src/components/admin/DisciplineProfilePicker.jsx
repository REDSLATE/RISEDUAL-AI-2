import React from 'react';
import { Shield, ShieldCheck, ShieldAlert, AlertTriangle, Loader2, Power, PowerOff } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * DisciplineProfilePicker — owner admin surface for the named
 * kill-switch profile registry.
 *
 * Renders, per asset_type (equity + crypto):
 *   - the active profile config (if any)
 *   - live session stats (today's realised P&L, consecutive losses)
 *   - the halt verdict + per-rule trigger messages
 *   - an Activate form (profile dropdown + starting equity) and a
 *     Deactivate button
 *
 * All endpoints are owner-only — see
 * `routes/admin_kill_switch_profile_runtime.py`.
 */
export default function DisciplineProfilePicker() {
  const [profiles, setProfiles] = React.useState([]);
  const [statusByAsset, setStatusByAsset] = React.useState({ equity: null, crypto: null });
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);

  const refresh = React.useCallback(async () => {
    setError(null);
    try {
      const [profilesRes, eqRes, crRes] = await Promise.all([
        authFetch(`${API}/admin/kill-switch/profiles`),
        authFetch(`${API}/admin/kill-switch/runtime/equity/status`),
        authFetch(`${API}/admin/kill-switch/runtime/crypto/status`),
      ]);
      if (!profilesRes.ok) throw new Error(`profiles HTTP ${profilesRes.status}`);
      if (!eqRes.ok) throw new Error(`equity status HTTP ${eqRes.status}`);
      if (!crRes.ok) throw new Error(`crypto status HTTP ${crRes.status}`);
      const [pBody, eqBody, crBody] = await Promise.all([
        profilesRes.json(), eqRes.json(), crRes.json(),
      ]);
      setProfiles(pBody.profiles || []);
      setStatusByAsset({ equity: eqBody, crypto: crBody });
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { refresh(); }, [refresh]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-400 p-6" data-testid="discipline-picker-loading">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading profile registry…
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-6 text-rose-300" data-testid="discipline-picker-error">
        Could not load profile registry: {error}
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="discipline-picker">
      <header>
        <h2 className="text-2xl font-semibold text-slate-100 flex items-center gap-2">
          <Shield className="w-6 h-6 text-violet-300" /> Discipline Profiles
        </h2>
        <p className="text-sm text-slate-400 mt-1 max-w-2xl">
          Named, code-versioned discipline overlays. Activating a
          profile against an asset core enforces its rules as a
          live gate before every paper-trade emission. Halts log
          the firing rule and skip the trade. Deactivate any time —
          the brain runs ungated by default.
        </p>
      </header>

      {/* Registry overview */}
      <section data-testid="discipline-registry">
        <h3 className="text-xs uppercase tracking-widest text-violet-300/70 mb-3">
          Registry · {profiles.length} profile{profiles.length === 1 ? '' : 's'}
        </h3>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {profiles.map((p) => (
            <article
              key={p.key}
              className="rounded-lg border border-slate-800 bg-slate-900/60 p-4"
              data-testid={`discipline-profile-${p.key}`}
            >
              <h4 className="text-sm font-semibold text-violet-200 mb-1">
                {p.name}
              </h4>
              <p className="text-xs text-slate-400 mb-3 leading-relaxed">
                {p.description}
              </p>
              <ul className="text-xs text-slate-300 space-y-1 mb-2">
                {p.rules.daily_max_loss_pct != null && (
                  <li>· Daily max loss: {(p.rules.daily_max_loss_pct * 100).toFixed(0)}% of equity</li>
                )}
                {p.rules.daily_max_loss_usd != null && (
                  <li>· Hard USD floor: ${p.rules.daily_max_loss_usd}</li>
                )}
                {p.rules.consecutive_loss_limit != null && (
                  <li>· Consecutive-loss limit: {p.rules.consecutive_loss_limit}</li>
                )}
                {p.rules.daily_profit_cap_pct != null && (
                  <li>· Profit cap: {(p.rules.daily_profit_cap_pct * 100).toFixed(0)}% of equity</li>
                )}
              </ul>
              <p className="text-[10px] text-slate-500 italic">
                {p.source}
              </p>
            </article>
          ))}
        </div>
      </section>

      {/* Live gate status — one card per asset_type */}
      <section className="grid sm:grid-cols-2 gap-4" data-testid="discipline-live-gate">
        <AssetGateCard
          assetType="equity"
          status={statusByAsset.equity}
          profiles={profiles}
          onChange={refresh}
        />
        <AssetGateCard
          assetType="crypto"
          status={statusByAsset.crypto}
          profiles={profiles}
          onChange={refresh}
        />
      </section>
    </div>
  );
}


function AssetGateCard({ assetType, status, profiles, onChange }) {
  const [profileKey, setProfileKey] = React.useState(profiles[0]?.key || '');
  const [startingEquity, setStartingEquity] = React.useState('1000');
  const [busy, setBusy] = React.useState(false);

  const active = !!status?.active;
  const halt = !!status?.halt;

  const activate = async () => {
    setBusy(true);
    try {
      const r = await authFetch(
        `${API}/admin/kill-switch/runtime/${assetType}/activate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            profile_key: profileKey,
            starting_equity_usd: parseFloat(startingEquity) || 0,
          }),
        },
      );
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body?.detail?.error || `HTTP ${r.status}`);
      }
      toast.success(`${assetType} discipline activated`);
      await onChange();
    } catch (e) {
      toast.error(`Activation failed: ${String(e?.message || e)}`);
    } finally {
      setBusy(false);
    }
  };

  const deactivate = async () => {
    setBusy(true);
    try {
      const r = await authFetch(
        `${API}/admin/kill-switch/runtime/${assetType}/active`,
        { method: 'DELETE' },
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      toast.success(`${assetType} discipline cleared`);
      await onChange();
    } catch (e) {
      toast.error(`Deactivation failed: ${String(e?.message || e)}`);
    } finally {
      setBusy(false);
    }
  };

  const labelColour =
    halt ? 'text-rose-300' : active ? 'text-emerald-300' : 'text-slate-400';
  const StatusIcon =
    halt ? ShieldAlert : active ? ShieldCheck : Shield;

  return (
    <div
      className={`rounded-lg border p-4 ${
        halt ? 'border-rose-500/40 bg-rose-500/5' : 'border-slate-800 bg-slate-900/60'
      }`}
      data-testid={`gate-card-${assetType}`}
    >
      <header className="flex items-center justify-between mb-3">
        <h3 className={`text-base font-semibold capitalize flex items-center gap-2 ${labelColour}`}>
          <StatusIcon className="w-4 h-4" /> {assetType}
        </h3>
        <span
          className={`text-[10px] uppercase tracking-wider font-semibold ${labelColour}`}
          data-testid={`gate-state-${assetType}`}
        >
          {halt ? 'HALT' : active ? 'ARMED' : 'INACTIVE'}
        </span>
      </header>

      {active && status?.config && (
        <div className="text-xs text-slate-300 space-y-1 mb-3">
          <div>Profile: <span className="text-violet-300">{status.config.profile_key}</span></div>
          <div>Starting equity: ${status.config.starting_equity_usd?.toFixed(2)}</div>
          {status.session && (
            <>
              <div>Today's P&amp;L: <span className={status.session.realized_pnl_usd_today >= 0 ? 'text-emerald-300' : 'text-rose-300'}>
                ${status.session.realized_pnl_usd_today?.toFixed(2)}
              </span></div>
              <div>Consec losses: {status.session.consecutive_losses_today}</div>
              <div>Closes today: {status.session.closes_today}</div>
            </>
          )}
        </div>
      )}

      {halt && status?.triggers?.length > 0 && (
        <div className="rounded border border-rose-500/30 bg-rose-500/10 p-2 mb-3" data-testid={`triggers-${assetType}`}>
          <div className="text-xs font-semibold text-rose-200 mb-1 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" /> Halt triggers
          </div>
          <ul className="text-xs text-rose-200/80 space-y-1">
            {status.triggers.map((t, i) => (
              <li key={i}>· {t.message}</li>
            ))}
          </ul>
        </div>
      )}

      {!active ? (
        <div className="space-y-2">
          <select
            value={profileKey}
            onChange={(e) => setProfileKey(e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-slate-200"
            data-testid={`profile-select-${assetType}`}
          >
            {profiles.map((p) => (
              <option key={p.key} value={p.key}>{p.name}</option>
            ))}
          </select>
          <Input
            type="number"
            value={startingEquity}
            onChange={(e) => setStartingEquity(e.target.value)}
            placeholder="Starting equity (USD)"
            className="bg-slate-900 border-slate-700 text-slate-200"
            data-testid={`equity-input-${assetType}`}
          />
          <Button
            onClick={activate}
            disabled={busy || !profileKey || !startingEquity}
            className="w-full bg-violet-600 hover:bg-violet-500 text-white"
            data-testid={`activate-btn-${assetType}`}
          >
            <Power className="w-4 h-4 mr-2" /> Activate
          </Button>
        </div>
      ) : (
        <Button
          onClick={deactivate}
          disabled={busy}
          className="w-full bg-slate-700 hover:bg-slate-600 text-slate-100"
          data-testid={`deactivate-btn-${assetType}`}
        >
          <PowerOff className="w-4 h-4 mr-2" /> Deactivate
        </Button>
      )}
    </div>
  );
}

export { DisciplineProfilePicker };
