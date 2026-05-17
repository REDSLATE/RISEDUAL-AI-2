import React from 'react';
import {
  Activity, AlertTriangle, BarChart3, BookOpenCheck,
  CheckCircle2, Cpu, Radio, ShieldQuestion, Sparkles, XCircle,
} from 'lucide-react';

/**
 * MC card renderer — turns a `card` payload from `/api/chat/mc/dispatch`
 * into an inline assistant bubble.
 *
 * Card kinds:
 *   - mc_help     → command catalog
 *   - mc_status   → sidecar liveness
 *   - mc_mirror   → honesty mirror aggregate
 *   - mc_intents  → recent shared_intents list
 *   - mc_opine    → fresh council hypothesis receipt
 *   - mc_error    → transport / parse failure
 *
 * Doctrinally these are read-only — buttons NEVER trigger MC writes.
 */

const Pill = ({ tone = 'slate', children, testid }) => {
  const toneMap = {
    teal: 'bg-[#3DE8D9]/15 text-[#3DE8D9] border-[#3DE8D9]/40',
    amber: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
    red: 'bg-red-500/15 text-red-300 border-red-500/40',
    green: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
    slate: 'bg-slate-700/40 text-slate-300 border-slate-500/40',
  };
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border ${toneMap[tone] || toneMap.slate}`}
      data-testid={testid}
    >
      {children}
    </span>
  );
};

const Row = ({ k, v, mono = false, testid }) => (
  <div className="flex justify-between gap-3 py-1 border-b border-slate-700/40 last:border-b-0" data-testid={testid}>
    <span className="text-[11px] text-slate-400">{k}</span>
    <span className={`text-[11px] text-slate-100 ${mono ? 'font-mono' : ''}`}>{v}</span>
  </div>
);

const StatusCard = ({ card }) => {
  if (card.error) {
    return (
      <div className="flex items-start gap-2 text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/40 rounded-md px-2.5 py-2">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
        <span>{card.error}</span>
      </div>
    );
  }
  const allUp = card.heartbeat_alive && card.contribution_alive && card.watchdog_alive;
  return (
    <div className="bg-slate-900/60 border border-slate-700/60 rounded-md p-2.5" data-testid="mc-card-status">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Radio className="w-3.5 h-3.5 text-[#3DE8D9]" />
          <span className="text-[12px] font-semibold text-white">MC Sidecar</span>
        </div>
        <Pill tone={allUp ? 'teal' : 'amber'} testid="mc-status-pill">
          {allUp ? 'live' : 'degraded'}
        </Pill>
      </div>
      <Row k="Heartbeat" v={card.heartbeat_alive ? '✓ alive' : '✗ dead'} testid="mc-status-heartbeat" />
      <Row k="Contribution" v={card.contribution_alive ? '✓ alive' : '✗ dead'} />
      <Row k="Watchdog" v={card.watchdog_alive ? '✓ alive' : '✗ dead'} />
      {typeof card.liveness_age_s === 'number' && (
        <Row k="Liveness age" v={`${card.liveness_age_s.toFixed(1)}s`} mono />
      )}
      {card.identity?.name && (
        <Row k="Brain" v={`${card.identity.name} v${card.identity.version || '?'}`} mono />
      )}
      {card.last_error && (
        <div className="mt-2 text-[10px] text-red-300 bg-red-500/10 px-2 py-1 rounded">
          last error: {card.last_error}
        </div>
      )}
    </div>
  );
};

const MirrorCard = ({ card }) => {
  if (card.error) {
    return (
      <div className="flex items-start gap-2 text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/40 rounded-md px-2.5 py-2">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
        <div>
          <div className="font-semibold">{card.error}</div>
          {card.detail && <div className="text-amber-300/80 mt-0.5 font-mono text-[10px]">{card.detail}</div>}
        </div>
      </div>
    );
  }
  const blocked = card.blocked_directional || 0;
  const total = card.total_intents || 0;
  const blockedPct = total ? Math.round((blocked / total) * 100) : 0;
  const reasons = Object.entries(card.by_reason || {}).slice(0, 5);
  return (
    <div className="bg-slate-900/60 border border-slate-700/60 rounded-md p-2.5" data-testid="mc-card-mirror">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <ShieldQuestion className="w-3.5 h-3.5 text-[#3DE8D9]" />
          <span className="text-[12px] font-semibold text-white">Honesty Mirror</span>
        </div>
        <Pill tone="slate">{card.hours}h window</Pill>
      </div>
      <Row k="Total intents" v={total} mono />
      <Row k="Blocked directional" v={`${blocked} (${blockedPct}%)`} mono />
      {reasons.length > 0 && (
        <div className="mt-2">
          <div className="text-[10px] text-slate-400 mb-1">Top hold reasons</div>
          <div className="flex flex-wrap gap-1">
            {reasons.map(([reason, count]) => (
              <Pill key={reason} tone="slate">{reason} · {count}</Pill>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const IntentsCard = ({ card }) => {
  if (card.error) {
    return (
      <div className="flex items-start gap-2 text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/40 rounded-md px-2.5 py-2">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
        <span>{card.error}</span>
      </div>
    );
  }
  return (
    <div className="bg-slate-900/60 border border-slate-700/60 rounded-md p-2.5" data-testid="mc-card-intents">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <BookOpenCheck className="w-3.5 h-3.5 text-[#3DE8D9]" />
          <span className="text-[12px] font-semibold text-white">Recent Intents</span>
        </div>
        <Pill tone={card.emission_enabled ? 'teal' : 'slate'}>
          {card.emission_enabled ? 'emitting' : 'observe-only'}
        </Pill>
      </div>
      {card.count === 0 ? (
        <div className="text-[11px] text-slate-400 py-2 text-center">
          {card.note || 'No intents in the audit log yet.'}
        </div>
      ) : (
        <div className="space-y-1.5">
          {card.intents.map((it, i) => (
            <div key={i} className="flex items-center justify-between gap-2 text-[11px]" data-testid={`mc-intent-row-${i}`}>
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono font-semibold text-white">{it.symbol}</span>
                <Pill tone={it.side === 'BUY' ? 'green' : it.side === 'SELL' || it.side === 'SHORT' ? 'red' : 'slate'}>
                  {it.side}
                </Pill>
              </div>
              <div className="text-slate-400 font-mono text-[10px]">
                {typeof it.confidence === 'number'
                  ? `${(it.confidence * 100).toFixed(0)}%`
                  : '—'}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const OpineCard = ({ card }) => {
  if (card.error) {
    return (
      <div className="flex items-start gap-2 text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/40 rounded-md px-2.5 py-2">
        <XCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
        <span>{card.error}</span>
      </div>
    );
  }
  const verdict = (card.display_action || card.verdict || 'HOLD').toUpperCase();
  const verdictTone = verdict === 'BUY' || verdict === 'COVER'
    ? 'green'
    : (verdict === 'SELL' || verdict === 'SHORT' ? 'red' : 'amber');
  const conf = typeof card.confidence === 'number'
    ? (card.confidence > 1 ? card.confidence : card.confidence * 100)
    : null;
  const conflict = card.raw_action && card.display_action
    && card.raw_action !== card.display_action;
  return (
    <div className="bg-slate-900/60 border border-slate-700/60 rounded-md p-2.5" data-testid="mc-card-opine">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Sparkles className="w-3.5 h-3.5 text-[#3DE8D9]" />
          <span className="text-[12px] font-semibold text-white">
            Council · <span className="font-mono">{card.symbol}</span>
          </span>
        </div>
        <Pill tone={verdictTone} testid="mc-opine-verdict">{verdict}</Pill>
      </div>
      {conf !== null && (
        <Row k="Confidence" v={`${conf.toFixed(0)}%`} mono testid="mc-opine-confidence" />
      )}
      {card.market_decision && (
        <Row k="Market judgment" v={card.market_decision} mono />
      )}
      {card.execution_decision && (
        <Row k="Execution gate" v={card.execution_decision} mono />
      )}
      {conflict && (
        <div className="mt-2 text-[10px] text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded px-2 py-1">
          <strong>Override active</strong>: market read {card.raw_action} →
          gated to {card.display_action}
          {card.hold_reason ? ` (${card.hold_reason})` : ''}
        </div>
      )}
      {card.summary && (
        <div className="mt-2 text-[11px] text-slate-300 leading-snug whitespace-pre-line">
          {card.summary}
        </div>
      )}
      <div className="mt-2 text-[10px] text-slate-500 italic">
        Read-only · chat does not execute. MC remains authority.
      </div>
    </div>
  );
};

const HelpCard = ({ card }) => (
  <div className="bg-slate-900/60 border border-slate-700/60 rounded-md p-2.5" data-testid="mc-card-help">
    <div className="flex items-center gap-1.5 mb-2">
      <Cpu className="w-3.5 h-3.5 text-[#3DE8D9]" />
      <span className="text-[12px] font-semibold text-white">MC commands</span>
    </div>
    {card.error && (
      <div className="text-[11px] text-amber-300 mb-2">{card.error}</div>
    )}
    <div className="space-y-1">
      {(card.commands || []).map((c, i) => (
        <div key={i} className="flex justify-between gap-2 text-[11px]">
          <span className="font-mono text-[#3DE8D9]">{c.cmd}</span>
          <span className="text-slate-400 text-right flex-1">{c.desc}</span>
        </div>
      ))}
    </div>
  </div>
);

const ErrorCard = ({ card }) => (
  <div className="flex items-start gap-2 text-[11px] text-red-200 bg-red-500/10 border border-red-500/40 rounded-md px-2.5 py-2">
    <XCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
    <span>{card.error || 'MC command failed.'}</span>
  </div>
);

const MCCard = ({ card }) => {
  if (!card || !card.kind) return null;
  switch (card.kind) {
    case 'mc_status': return <StatusCard card={card} />;
    case 'mc_mirror': return <MirrorCard card={card} />;
    case 'mc_intents': return <IntentsCard card={card} />;
    case 'mc_opine': return <OpineCard card={card} />;
    case 'mc_help': return <HelpCard card={card} />;
    case 'mc_error': return <ErrorCard card={card} />;
    default:
      return (
        <div className="text-[11px] text-slate-400">
          Unknown MC card kind: <code className="font-mono">{card.kind}</code>
        </div>
      );
  }
};

export default MCCard;
export { Pill, Row };
