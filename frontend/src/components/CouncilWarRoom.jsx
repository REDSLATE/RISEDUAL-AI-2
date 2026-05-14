import React, { useState, useEffect, useCallback } from 'react';
import { Brain, Shield, Swords, Eye, ChevronRight, Activity, AlertCircle } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

// Per-brain visual identity. Matches MC's roles-manifest:
//   alpha    → trader     (has hands)
//   camaro   → challenger (has teeth)
//   chevelle → governor   (has the keys)
//   redeye   → opponent   (argues contrary)
const BRAIN_VISUALS = {
  alpha: {
    icon: Brain,
    accent: 'from-emerald-500 to-emerald-700',
    ring: 'ring-emerald-500/40',
    chipBg: 'bg-emerald-900/30',
    chipText: 'text-emerald-300',
    chipBorder: 'border-emerald-700/40',
  },
  camaro: {
    icon: Swords,
    accent: 'from-amber-500 to-orange-700',
    ring: 'ring-amber-500/40',
    chipBg: 'bg-amber-900/30',
    chipText: 'text-amber-300',
    chipBorder: 'border-amber-700/40',
  },
  chevelle: {
    icon: Shield,
    accent: 'from-sky-500 to-indigo-700',
    ring: 'ring-sky-500/40',
    chipBg: 'bg-sky-900/30',
    chipText: 'text-sky-300',
    chipBorder: 'border-sky-700/40',
  },
  redeye: {
    icon: Eye,
    accent: 'from-rose-500 to-red-800',
    ring: 'ring-rose-500/40',
    chipBg: 'bg-rose-900/30',
    chipText: 'text-rose-300',
    chipBorder: 'border-rose-700/40',
  },
};

const STANCE_TINT = {
  bull: 'text-emerald-400',
  long: 'text-emerald-400',
  bear: 'text-rose-400',
  short: 'text-rose-400',
  observation: 'text-sky-300',
  abstain: 'text-slate-400',
};

function relTime(iso) {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function BrainCard({ role, opinions }) {
  const v = BRAIN_VISUALS[role.runtime] || BRAIN_VISUALS.alpha;
  const Icon = v.icon;
  const myOpinions = opinions.filter((o) => o.runtime === role.runtime);
  const latest = myOpinions[0] || null;
  const stanceClass = latest ? STANCE_TINT[(latest.stance || '').toLowerCase()] || 'text-slate-300' : 'text-slate-500';

  return (
    <Card
      className={`bg-slate-900/60 border-slate-700/60 ring-1 ${v.ring} p-4 sm:p-5 flex flex-col gap-3`}
      data-testid={`council-brain-${role.runtime}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className={`w-11 h-11 rounded-xl bg-gradient-to-br ${v.accent} flex items-center justify-center shadow-md`}>
            <Icon className="w-6 h-6 text-white" />
          </div>
          <div>
            <h3 className="text-white text-lg font-bold capitalize tracking-tight" style={{ fontFamily: 'Manrope, sans-serif' }}>
              {role.runtime}
            </h3>
            <p className="text-slate-400 text-xs">{role.title} · <span className="italic">{role.tagline}</span></p>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge className={`${v.chipBg} ${v.chipText} ${v.chipBorder}`}>
            {role.authority_state || 'idle'}
          </Badge>
          <span className="text-slate-500 text-[10px] uppercase tracking-wide">
            seen {relTime(role.last_seen)}
          </span>
        </div>
      </div>

      <p className="text-slate-400 text-xs leading-relaxed line-clamp-3">
        {role.description}
      </p>

      <div className="border-t border-slate-700/40 pt-3 flex-1 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-slate-500 text-xs uppercase tracking-wide">Latest take</span>
          {latest?.confidence != null && (
            <span className="text-slate-300 text-xs">
              conf {Number(latest.confidence).toFixed(2)}
            </span>
          )}
        </div>
        {latest ? (
          <>
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-sm font-bold uppercase ${stanceClass}`}>{latest.stance || '—'}</span>
              {latest.topic && (
                <span className="text-slate-400 text-xs">on {latest.topic.replace('symbol:', '$')}</span>
              )}
              <span className="text-slate-500 text-xs">· {relTime(latest.created_at)}</span>
            </div>
            <p className="text-slate-200 text-sm leading-snug line-clamp-3 whitespace-pre-wrap">
              {latest.body || '(no body)'}
            </p>
            {(latest.evidence_keys || []).length > 0 && (
              <div className="flex items-center gap-1 flex-wrap pt-1">
                {(latest.evidence_keys || []).slice(0, 4).map((k) => (
                  <span key={k} className="text-[10px] bg-slate-800/80 text-slate-400 px-1.5 py-0.5 rounded border border-slate-700/40">
                    {k}
                  </span>
                ))}
                {(latest.evidence_keys || []).length > 4 && (
                  <span className="text-[10px] text-slate-500">
                    +{(latest.evidence_keys || []).length - 4} more
                  </span>
                )}
              </div>
            )}
          </>
        ) : (
          <p className="text-slate-500 text-sm italic">
            {role.runtime} hasn't posted yet — quiet in this slice.
          </p>
        )}
      </div>

      <div className="border-t border-slate-700/40 pt-2 flex items-center justify-between text-xs">
        <span className="text-slate-500">
          {myOpinions.length} recent {myOpinions.length === 1 ? 'opinion' : 'opinions'}
        </span>
        {role.may_execute && (
          <Badge className="bg-emerald-700/40 text-emerald-300 border-emerald-600/40">
            Executor seat
          </Badge>
        )}
      </div>
    </Card>
  );
}

/**
 * CouncilWarRoom — the live 4-brain council, replacing the legacy
 * Strategist/Auditor pair in the War Room.
 *
 * Reads /api/intelligence/council (global) or /api/intelligence/council/{symbol}
 * (symbol-filtered). Auto-refreshes every 60s.
 */
export default function CouncilWarRoom({ initialSymbol = '' }) {
  const { user } = useAuth();  // for authFetch
  const [symbol, setSymbol] = useState(initialSymbol);
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (sym) => {
    setLoading(true);
    setError('');
    try {
      const url = sym
        ? `${API}/intelligence/council/${sym.trim().toUpperCase()}`
        : `${API}/intelligence/council`;
      const fetchFn = user ? authFetch : fetch;
      const res = await fetchFn(url);
      if (!res.ok) throw new Error(`Server error (${res.status})`);
      setSnapshot(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [user]);

  // Initial load + auto-refresh.
  useEffect(() => {
    load(symbol);
    const timer = setInterval(() => load(symbol), 60_000);
    return () => clearInterval(timer);
  }, [load, symbol]);

  const onSubmit = (e) => {
    e?.preventDefault();
    load(symbol);
  };

  const roles = snapshot?.roles || [];
  const opinions = snapshot?.opinions || [];
  const sc = snapshot?.scorecard?.summary;

  return (
    <div className="space-y-6" data-testid="council-war-room">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-indigo-500 via-sky-500 to-emerald-500 flex items-center justify-center shadow-lg">
            <Activity className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope, sans-serif' }}>
              Council War Room
            </h2>
            <p className="text-slate-300 text-xs sm:text-sm">
              Live 4-brain discussion from Mission Control · alpha · camaro · chevelle · redeye
            </p>
          </div>
        </div>

        {/* Alpha's scorecard chip (us) */}
        {sc && (
          <div className="flex items-center gap-2 bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2">
            <span className="text-slate-400 text-xs">alpha record</span>
            <span className="text-white font-bold text-sm">
              {sc.wins}W · {sc.losses}L
            </span>
            <span className="text-emerald-400 text-xs">
              {sc.hit_rate != null ? `${(Number(sc.hit_rate) * 100).toFixed(1)}%` : '—'}
            </span>
          </div>
        )}
      </div>

      {/* Symbol filter */}
      <form onSubmit={onSubmit} className="flex items-center gap-2" data-testid="council-symbol-form">
        <Input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Filter by symbol (optional) — e.g. NVDA"
          className="bg-slate-900/60 border-slate-700 text-white max-w-xs"
          data-testid="council-symbol-input"
        />
        <Button type="submit" disabled={loading} data-testid="council-load-btn">
          {loading ? 'Loading…' : (symbol ? 'Focus' : 'Refresh')}
        </Button>
        {symbol && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => { setSymbol(''); load(''); }}
            data-testid="council-clear-btn"
          >
            Clear
          </Button>
        )}
      </form>

      {/* Doctrine + errors */}
      {snapshot?.doctrine && (
        <p className="text-slate-500 text-xs italic">{snapshot.doctrine}</p>
      )}
      {error && (
        <div className="flex items-center gap-2 text-rose-400 text-sm" data-testid="council-error">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}
      {snapshot?.errors?.opinions && (
        <div className="text-amber-400 text-xs">
          opinions feed: {String(snapshot.errors.opinions).slice(0, 200)}
        </div>
      )}

      {/* The 4 brain cards */}
      {roles.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {roles.map((role) => (
            <BrainCard key={role.runtime} role={role} opinions={opinions} />
          ))}
        </div>
      ) : !loading && (
        <div className="text-slate-400 text-sm py-12 text-center" data-testid="council-empty">
          No council members reporting. Waiting for Mission Control…
        </div>
      )}

      {/* Recent discussion thread */}
      {opinions.length > 0 && (
        <Card className="bg-slate-900/40 border-slate-700/60 p-4" data-testid="council-thread">
          <div className="flex items-center gap-2 mb-3">
            <ChevronRight className="w-4 h-4 text-slate-400" />
            <h3 className="text-white text-sm font-bold uppercase tracking-wide">
              Recent discussion (balanced, max 5/brain)
            </h3>
            <span className="ml-auto text-slate-500 text-xs">
              {snapshot.opinion_count_returned} shown / {snapshot.opinion_count_total} total
            </span>
          </div>
          <div className="space-y-3">
            {opinions.map((op) => {
              const v = BRAIN_VISUALS[op.runtime] || BRAIN_VISUALS.alpha;
              return (
                <div key={op.id} className="flex items-start gap-3 border-l-2 pl-3" style={{ borderColor: 'rgb(71 85 105 / 0.4)' }} data-testid={`council-thread-row-${op.id}`}>
                  <span className={`text-xs font-bold uppercase ${v.chipText}`}>{op.runtime}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-slate-300 text-xs flex items-center gap-2 mb-0.5">
                      <span className="capitalize">{op.stance || '—'}</span>
                      {op.topic && <span className="text-slate-500">on {op.topic.replace('symbol:', '$')}</span>}
                      <span className="text-slate-500">· {relTime(op.created_at)}</span>
                      {op.confidence != null && (
                        <span className="text-slate-500">· conf {Number(op.confidence).toFixed(2)}</span>
                      )}
                    </p>
                    <p className="text-slate-200 text-sm line-clamp-2 whitespace-pre-wrap">{op.body || '(no body)'}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}
