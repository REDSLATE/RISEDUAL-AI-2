import React from 'react';
import {
  Brain, ScrollText, RefreshCw, ChevronDown, ChevronRight,
  TrendingUp, TrendingDown, Minus, Trophy, AlertTriangle,
  CheckCircle2, XCircle,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Stage 3 Decision Pairs Dashboard.
 *
 * Side-by-side Sovereign (deterministic 6-submodel ML) vs Council
 * (4-LLM consensus) for every closed paper trade. Read-only —
 * operator uses this to decide whether Sovereign is ready for
 * promotion (Stage 4).
 *
 * Wired to two owner-only endpoints:
 *   GET /api/admin/decision-pairs        — paginated rows
 *   GET /api/admin/decision-pairs/stats  — aggregate scoreboard
 */
export default function Stage3DecisionPairs() {
  const [stats, setStats] = React.useState(null);
  const [rows, setRows] = React.useState([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [lane, setLane] = React.useState('');
  const [agreement, setAgreement] = React.useState('');
  const [resolved, setResolved] = React.useState('');
  const [sinceDays, setSinceDays] = React.useState(30);
  const [expanded, setExpanded] = React.useState(new Set());

  const fetchAll = React.useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const qs = new URLSearchParams();
      if (lane) qs.set('lane', lane);
      if (agreement) qs.set('agreement', agreement);
      if (resolved !== '') qs.set('resolved', resolved);
      qs.set('limit', '100');

      const statsQs = new URLSearchParams();
      if (lane) statsQs.set('lane', lane);
      statsQs.set('since_days', String(sinceDays));

      const [pairsRes, statsRes] = await Promise.all([
        authFetch(`${API}/admin/decision-pairs?${qs.toString()}`),
        authFetch(`${API}/admin/decision-pairs/stats?${statsQs.toString()}`),
      ]);
      const pj = await pairsRes.json();
      const sj = await statsRes.json();
      if (!pairsRes.ok) throw new Error(pj.detail || 'pairs fetch failed');
      if (!statsRes.ok) throw new Error(sj.detail || 'stats fetch failed');
      setRows(pj.rows || []);
      setStats(sj);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [lane, agreement, resolved, sinceDays]);

  React.useEffect(() => { fetchAll(); }, [fetchAll]);

  const toggleExpand = (decisionId) => {
    const next = new Set(expanded);
    if (next.has(decisionId)) next.delete(decisionId); else next.add(decisionId);
    setExpanded(next);
  };

  return (
    <div className="space-y-6" data-testid="stage3-decision-pairs">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-semibold text-zinc-100 flex items-center gap-2">
            <Trophy className="w-4 h-4 text-amber-400" />
            Stage 3 — Sovereign vs Council Ledger
          </h2>
          <p className="text-sm text-zinc-500 max-w-2xl mt-1">
            Side-by-side verdicts from the deterministic 6-submodel Sovereign AI and the
            4-LLM Council. Outcomes are backfilled when paper trades close.
            Promotion to Stage 4 requires Sovereign accuracy ≥ Council with statistical
            confidence over the last 30 days.
          </p>
        </div>
        <button
          onClick={fetchAll}
          disabled={busy}
          data-testid="stage3-refresh-btn"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-sm border border-zinc-700 disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${busy ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <FilterChip label="Lane" value={lane} onChange={setLane} options={[
          ['', 'All'], ['equity', 'Equity'], ['crypto', 'Crypto'],
        ]} testId="stage3-filter-lane" />
        <FilterChip label="Agreement" value={agreement} onChange={setAgreement} options={[
          ['', 'All'], ['AGREE', 'Agree'], ['PARTIAL', 'Partial'], ['DISAGREE', 'Disagree'],
        ]} testId="stage3-filter-agreement" />
        <FilterChip label="Resolved" value={resolved} onChange={setResolved} options={[
          ['', 'All'], ['true', 'Closed'], ['false', 'Open'],
        ]} testId="stage3-filter-resolved" />
        <FilterChip label="Window" value={String(sinceDays)} onChange={(v) => setSinceDays(Number(v) || 30)} options={[
          ['7', '7d'], ['30', '30d'], ['90', '90d'], ['365', '1y'],
        ]} testId="stage3-filter-window" />
      </div>

      {error && (
        <div className="rounded-md border border-rose-900/60 bg-rose-950/30 p-3 text-sm text-rose-300 flex items-center gap-2" data-testid="stage3-error">
          <AlertTriangle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Scoreboard */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="stage3-scoreboard">
          <StatTile
            label="Sovereign Accuracy"
            value={`${(stats.sovereign.accuracy * 100).toFixed(1)}%`}
            sub={`${stats.sovereign.correct} / ${stats.resolved} resolved`}
            tone="emerald"
            icon={<Brain className="w-4 h-4" />}
            testId="stage3-sovereign-acc"
          />
          <StatTile
            label="Council Accuracy"
            value={`${(stats.council.accuracy * 100).toFixed(1)}%`}
            sub={`${stats.council.correct} / ${stats.resolved} resolved`}
            tone="indigo"
            icon={<ScrollText className="w-4 h-4" />}
            testId="stage3-council-acc"
          />
          <StatTile
            label="Agreement"
            value={`${(stats.agreement.agree_rate * 100).toFixed(0)}%`}
            sub={`${stats.agreement.agree} agree · ${stats.agreement.disagree} disagree`}
            tone="amber"
            icon={<TrendingUp className="w-4 h-4" />}
            testId="stage3-agreement"
          />
          <StatTile
            label="Total Pairs"
            value={String(stats.total_pairs)}
            sub={`${stats.unresolved} open · ${stats.resolved} closed`}
            tone="zinc"
            icon={<Trophy className="w-4 h-4" />}
            testId="stage3-totals"
          />
        </div>
      )}

      {/* Head-to-head */}
      {stats && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4" data-testid="stage3-winners">
          <div className="text-xs uppercase tracking-wide text-zinc-500 mb-3">
            Head-to-head (resolved pairs · last {stats.since_days} days)
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <WinnerCell label="Sovereign won" count={stats.winners.sovereign} total={stats.resolved} tone="emerald" />
            <WinnerCell label="Council won" count={stats.winners.council} total={stats.resolved} tone="indigo" />
            <WinnerCell label="Both right (tie)" count={stats.winners.tie} total={stats.resolved} tone="amber" />
            <WinnerCell label="Both wrong" count={stats.winners.neither} total={stats.resolved} tone="rose" />
          </div>
        </div>
      )}

      {/* Pair ledger */}
      <div className="rounded-lg border border-zinc-800 overflow-hidden" data-testid="stage3-rows">
        <div className="grid grid-cols-[1.2fr_1.2fr_1fr_0.6fr] gap-3 px-4 py-2 bg-zinc-900 border-b border-zinc-800 text-[10px] uppercase tracking-wide text-zinc-500">
          <div>Sovereign voice</div>
          <div>Council voice</div>
          <div>Outcome</div>
          <div className="text-right">Winner</div>
        </div>
        {rows.length === 0 && !busy && (
          <div className="px-4 py-10 text-center text-sm text-zinc-500" data-testid="stage3-empty">
            No decision pairs in this window. Pairs are filed when Sovereign runs alongside
            a paper trade open.
          </div>
        )}
        {rows.map((r) => (
          <PairRow
            key={r.decision_id}
            row={r}
            expanded={expanded.has(r.decision_id)}
            onToggle={() => toggleExpand(r.decision_id)}
          />
        ))}
      </div>
    </div>
  );
}

function FilterChip({ label, value, onChange, options, testId }) {
  return (
    <label className="inline-flex items-center gap-1.5 bg-zinc-900 border border-zinc-800 rounded-md px-2 py-1">
      <span className="text-zinc-500">{label}:</span>
      <select
        data-testid={testId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-zinc-200 outline-none cursor-pointer"
      >
        {options.map(([v, lbl]) => (
          <option key={v} value={v} className="bg-zinc-900">{lbl}</option>
        ))}
      </select>
    </label>
  );
}

function StatTile({ label, value, sub, tone, icon, testId }) {
  const tones = {
    emerald: 'border-emerald-900/60 bg-emerald-950/20 text-emerald-300',
    indigo: 'border-indigo-900/60 bg-indigo-950/20 text-indigo-300',
    amber: 'border-amber-900/60 bg-amber-950/20 text-amber-300',
    rose: 'border-rose-900/60 bg-rose-950/20 text-rose-300',
    zinc: 'border-zinc-800 bg-zinc-900/50 text-zinc-300',
  };
  return (
    <div className={`rounded-lg border ${tones[tone] || tones.zinc} p-3`} data-testid={testId}>
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide opacity-70">
        {icon}{label}
      </div>
      <div className="text-2xl font-semibold mt-1 text-zinc-100">{value}</div>
      <div className="text-[11px] opacity-60 mt-0.5">{sub}</div>
    </div>
  );
}

function WinnerCell({ label, count, total, tone }) {
  const pct = total ? ((count / total) * 100).toFixed(0) : '0';
  const tones = {
    emerald: 'text-emerald-300',
    indigo: 'text-indigo-300',
    amber: 'text-amber-300',
    rose: 'text-rose-300',
  };
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-zinc-400">{label}</span>
      <span className={`text-sm font-semibold ${tones[tone] || 'text-zinc-200'}`}>
        {count} <span className="text-[10px] opacity-60">({pct}%)</span>
      </span>
    </div>
  );
}

function PairRow({ row, expanded, onToggle }) {
  const sov = row.sovereign || {};
  const council = row.council || {};
  const outcome = row.outcome;
  const winner = outcome?.scoreboard?.winner;

  return (
    <div className="border-b border-zinc-800/60 last:border-b-0" data-testid={`stage3-row-${row.decision_id}`}>
      <button
        onClick={onToggle}
        className="w-full grid grid-cols-[1.2fr_1.2fr_1fr_0.6fr] gap-3 px-4 py-3 hover:bg-zinc-900/40 text-left text-sm"
      >
        <VoiceCell action={sov.action} confidence={sov.confidence} />
        <VoiceCell action={council.action} confidence={council.confidence} />
        <OutcomeCell outcome={outcome} resolved={row.resolved} />
        <div className="text-right flex items-center justify-end gap-1.5">
          <WinnerBadge winner={winner} resolved={row.resolved} />
          {expanded ? <ChevronDown className="w-4 h-4 text-zinc-500" /> : <ChevronRight className="w-4 h-4 text-zinc-500" />}
        </div>
      </button>
      {expanded && (
        <div className="px-4 pb-4 pt-1 grid grid-cols-1 md:grid-cols-2 gap-3 text-xs text-zinc-400 bg-zinc-950/40">
          <VoiceDetail title="Sovereign rationale" voice={sov} />
          <VoiceDetail title="Council rationale" voice={council} />
          <div className="md:col-span-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500 pt-2 border-t border-zinc-800/60">
            <span>symbol: <span className="text-zinc-300">{row.symbol}</span></span>
            <span>lane: <span className="text-zinc-300">{row.lane}</span></span>
            <span>agreement: <span className="text-zinc-300">{row.agreement}</span></span>
            {row.trade_id && <span>trade_id: <span className="text-zinc-300 font-mono">{row.trade_id.slice(0, 8)}</span></span>}
            {row.trace_id && <span>trace: <span className="text-zinc-300 font-mono">{row.trace_id}</span></span>}
            <span>created: <span className="text-zinc-300">{row.created_at ? new Date(row.created_at).toLocaleString() : '—'}</span></span>
          </div>
        </div>
      )}
    </div>
  );
}

function VoiceCell({ action, confidence }) {
  const a = (action || 'UNKNOWN').toUpperCase();
  const conf = typeof confidence === 'number' ? confidence : 0;
  let Icon = Minus;
  let cls = 'text-zinc-400';
  if (a === 'LONG' || a === 'BUY') { Icon = TrendingUp; cls = 'text-emerald-400'; }
  else if (a === 'SHORT' || a === 'SELL') { Icon = TrendingDown; cls = 'text-rose-400'; }
  else if (a === 'HOLD') { cls = 'text-zinc-400'; }
  else { cls = 'text-zinc-600'; }
  return (
    <div className="flex items-center gap-2">
      <Icon className={`w-4 h-4 ${cls}`} />
      <span className={`font-medium ${cls}`}>{a}</span>
      <span className="text-zinc-500 text-xs">{conf > 0 ? `${(conf * 100).toFixed(0)}%` : ''}</span>
    </div>
  );
}

function OutcomeCell({ outcome, resolved }) {
  if (!resolved || !outcome) {
    return <span className="text-xs text-zinc-600">open</span>;
  }
  const label = outcome.outcome_label;
  const pnl = outcome.pnl_usd;
  const cls = label === 'win' ? 'text-emerald-400' : label === 'loss' ? 'text-rose-400' : 'text-zinc-400';
  return (
    <div className="flex items-center gap-2">
      <span className={`text-xs uppercase font-medium ${cls}`}>{label}</span>
      <span className={`text-xs ${pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
        {pnl >= 0 ? '+' : ''}${pnl?.toFixed(2)}
      </span>
    </div>
  );
}

function WinnerBadge({ winner, resolved }) {
  if (!resolved) return <span className="text-[10px] text-zinc-600 uppercase">—</span>;
  const tones = {
    sovereign: 'bg-emerald-950/40 text-emerald-300 border-emerald-900/60',
    council: 'bg-indigo-950/40 text-indigo-300 border-indigo-900/60',
    tie: 'bg-amber-950/40 text-amber-300 border-amber-900/60',
    neither: 'bg-rose-950/40 text-rose-300 border-rose-900/60',
  };
  const labels = { sovereign: 'Sov', council: 'Council', tie: 'Tie', neither: 'Both wrong' };
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${tones[winner] || 'border-zinc-800 text-zinc-500'}`}>
      {labels[winner] || winner || '?'}
    </span>
  );
}

function VoiceDetail({ title, voice }) {
  return (
    <div className="rounded-md bg-zinc-900/60 border border-zinc-800 p-3">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">{title}</div>
      <div className="text-zinc-300 leading-relaxed whitespace-pre-wrap">
        {voice?.rationale_text || <span className="text-zinc-600 italic">absent</span>}
      </div>
      {voice?.key_drivers?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {voice.key_drivers.map((d, i) => (
            <span key={i} className="text-[10px] bg-emerald-950/40 border border-emerald-900/60 text-emerald-300 px-1.5 py-0.5 rounded inline-flex items-center gap-1">
              <CheckCircle2 className="w-2.5 h-2.5" />
              {d.submodel} {d.score?.toFixed?.(2)}
            </span>
          ))}
        </div>
      )}
      {voice?.warnings?.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {voice.warnings.map((w, i) => (
            <span key={i} className="text-[10px] bg-amber-950/40 border border-amber-900/60 text-amber-300 px-1.5 py-0.5 rounded inline-flex items-center gap-1">
              <AlertTriangle className="w-2.5 h-2.5" />
              {w}
            </span>
          ))}
        </div>
      )}
      {voice?.dissent_flags?.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {voice.dissent_flags.map((d, i) => (
            <span key={i} className="text-[10px] bg-zinc-800/60 border border-zinc-700 text-zinc-400 px-1.5 py-0.5 rounded inline-flex items-center gap-1">
              <XCircle className="w-2.5 h-2.5" />
              {d.submodel}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
