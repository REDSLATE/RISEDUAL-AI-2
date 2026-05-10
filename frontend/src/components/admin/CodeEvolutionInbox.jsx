import React from 'react';
import {
  ShieldCheck, RefreshCw, AlertTriangle, CheckCircle2, XCircle,
  Lock, FileWarning, Inbox, Pen,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * CodeEvolutionInbox — RISEDUAL Code Evolution v0 operator UI.
 *
 * Read-only presentation of the `code_evolution_receipts` collection
 * + a single mutation surface (countersign or reject). The doctrine
 * is enforced server-side: this card has NO authority of its own —
 * it just renders what the v0 gate already decided.
 *
 *   POST /api/admin/code-evolution/countersign  → operator approve / reject
 *   GET  /api/admin/code-evolution/receipts     → list (sorted desc)
 */

const STATUS_PILL = {
  PROPOSED: {
    label: 'Proposed', cls: 'bg-slate-700/30 text-slate-300 border-slate-600/40',
  },
  REQUIRES_OPERATOR_SIGNATURE: {
    label: 'Needs 1 sig', cls: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  },
  REQUIRES_DUAL_OPERATOR_SIGNATURE: {
    label: 'Needs 2 sigs', cls: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
  },
  SIGNED_AWAITING_OPS: {
    label: 'Signed · awaiting ops', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  },
  BLOCKED_FORBIDDEN_PATTERN: {
    label: 'Blocked · pattern', cls: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
  },
  BLOCKED_OPERATOR_ONLY: {
    label: 'Blocked · gate', cls: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  },
  REJECTED: {
    label: 'Rejected', cls: 'bg-slate-800 text-slate-500 border-slate-700/40',
  },
};

const RISK_PILL = {
  LOW:      'bg-slate-800 text-slate-400 border-slate-700/40',
  MEDIUM:   'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
  HIGH:     'bg-amber-500/10 text-amber-400 border-amber-500/30',
  CRITICAL: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
};

function StatusPill({ status }) {
  const meta = STATUS_PILL[status] || {
    label: status, cls: 'bg-slate-800 text-slate-400 border-slate-700/40',
  };
  return (
    <span
      data-testid={`code-evo-status-${status}`}
      className={`inline-block px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-mono border ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
}

function RiskPill({ risk }) {
  return (
    <span
      data-testid={`code-evo-risk-${risk}`}
      className={`inline-block px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-mono border ${RISK_PILL[risk] || RISK_PILL.MEDIUM}`}
    >
      {risk}
    </span>
  );
}

function SigsRatio({ have, need }) {
  const ratio = `${have}/${need}`;
  const cls = need === 0
    ? 'text-slate-600'
    : have >= need ? 'text-emerald-400' : 'text-amber-400';
  return (
    <span className={`font-mono text-xs ${cls}`} data-testid="code-evo-sigs-ratio">
      {ratio}
    </span>
  );
}

function ReceiptRow({ receipt, onApprove, onReject, busyId }) {
  const blocked = receipt.status === 'BLOCKED_OPERATOR_ONLY'
    || receipt.status === 'BLOCKED_FORBIDDEN_PATTERN'
    || receipt.status === 'REJECTED';
  const fullySigned = receipt.status === 'SIGNED_AWAITING_OPS';
  const canSign = !blocked && !fullySigned;
  const isBusy = busyId === receipt.patch_id;

  return (
    <tr
      key={receipt.patch_id}
      data-testid={`code-evo-row-${receipt.patch_id}`}
      className="border-t border-slate-800 hover:bg-slate-900/40 transition-colors"
    >
      <td className="py-3 px-3 align-top">
        <div className="font-mono text-xs text-slate-200" data-testid="code-evo-patch-id">
          {receipt.patch_id}
        </div>
        <div className="text-[11px] text-slate-500 mt-0.5 line-clamp-1">
          {receipt.title}
        </div>
        {receipt.target_files?.length > 0 && (
          <div className="text-[10px] text-slate-600 mt-1 font-mono line-clamp-1">
            {receipt.target_files.slice(0, 2).join(' · ')}
            {receipt.target_files.length > 2 && ` +${receipt.target_files.length - 2}`}
          </div>
        )}
      </td>

      <td className="py-3 px-3 align-top">
        <div className="flex flex-col gap-1.5">
          <StatusPill status={receipt.status} />
          <RiskPill risk={receipt.risk_level} />
        </div>
      </td>

      <td className="py-3 px-3 align-top">
        <SigsRatio
          have={(receipt.signatures || []).length}
          need={receipt.required_signatures || 0}
        />
        {(receipt.signatures || []).length > 0 && (
          <div className="text-[10px] text-slate-600 mt-1 font-mono line-clamp-1">
            {(receipt.signatures || []).map((s) => s.operator_email).join(', ')}
          </div>
        )}
      </td>

      <td className="py-3 px-3 align-top">
        {blocked ? (
          <div className="flex items-center gap-1.5 text-[11px] text-rose-400/80">
            <Lock size={12} />
            <span>locked</span>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid={`code-evo-approve-${receipt.patch_id}`}
              disabled={!canSign || isBusy}
              onClick={() => onApprove(receipt)}
              className={`px-2.5 py-1 rounded text-[11px] font-semibold border transition-colors flex items-center gap-1 ${
                canSign && !isBusy
                  ? 'bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                  : 'bg-slate-800 text-slate-600 border-slate-700/40 cursor-not-allowed'
              }`}
            >
              <Pen size={11} />
              Sign
            </button>
            <button
              type="button"
              data-testid={`code-evo-reject-${receipt.patch_id}`}
              disabled={isBusy}
              onClick={() => onReject(receipt)}
              className={`px-2.5 py-1 rounded text-[11px] font-semibold border transition-colors flex items-center gap-1 ${
                !isBusy
                  ? 'bg-rose-500/5 hover:bg-rose-500/15 text-rose-400 border-rose-500/30'
                  : 'bg-slate-800 text-slate-600 border-slate-700/40 cursor-not-allowed'
              }`}
            >
              <XCircle size={11} />
              Reject
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

export default function CodeEvolutionInbox() {
  const [receipts, setReceipts] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [busyId, setBusyId] = React.useState(null);
  const [toast, setToast] = React.useState(null);

  const fetchReceipts = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await authFetch(`${API}/admin/code-evolution/receipts?limit=50`);
      if (!r.ok) throw new Error(`receipts fetch failed (${r.status})`);
      const json = await r.json();
      setReceipts(json.receipts || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { fetchReceipts(); }, [fetchReceipts]);

  const _act = async ({ patch_id, decision, note }) => {
    setBusyId(patch_id);
    setToast(null);
    setError(null);
    try {
      const r = await authFetch(`${API}/admin/code-evolution/countersign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patch_id, decision, note: note || '' }),
      });
      const json = await r.json();
      if (!r.ok) {
        throw new Error(json.detail || `${decision} failed (${r.status})`);
      }
      setToast({
        type: 'ok',
        text: decision === 'APPROVE'
          ? `Signed ${patch_id} · status now ${json.status}`
          : `Rejected ${patch_id}`,
      });
      await fetchReceipts();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  };

  const onApprove = (receipt) => {
    const note = window.prompt(
      `Sign patch ${receipt.patch_id}?\n\nOptional note (will be stored on the receipt):`,
      '',
    );
    if (note === null) return; // operator hit Cancel
    _act({ patch_id: receipt.patch_id, decision: 'APPROVE', note });
  };

  const onReject = (receipt) => {
    const note = window.prompt(
      `Reject patch ${receipt.patch_id}?\n\nReason (required for the audit trail):`,
      '',
    );
    if (note === null) return;
    if (!note.trim()) {
      setError('Reject reason is required.');
      return;
    }
    _act({ patch_id: receipt.patch_id, decision: 'REJECT', note });
  };

  const counts = receipts.reduce((m, r) => {
    m[r.status] = (m[r.status] || 0) + 1;
    return m;
  }, {});

  return (
    <div data-testid="code-evolution-inbox" className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-100 mb-1 flex items-center gap-2">
            <Inbox className="text-cyan-400" size={22} />
            Code Evolution — Inbox
          </h2>
          <p className="text-sm text-slate-400 max-w-2xl">
            Patches reviewed by RISEDUAL's v0 self-review gate. AI may
            audit, classify, and recommend tests; AI may NOT promote
            code.
            <span className="text-amber-400">
              {' '}Operator countersigns required for HIGH (1) and
              CRITICAL (2). Patches that touch the gate itself are
              permanently locked from this UI.
            </span>
          </p>
        </div>
        <button
          type="button"
          data-testid="code-evo-refresh-btn"
          disabled={loading}
          onClick={fetchReceipts}
          className={`px-4 py-2 rounded-md text-sm font-semibold transition-colors flex items-center gap-2 ${
            loading
              ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
              : 'bg-cyan-500 hover:bg-cyan-400 text-slate-950'
          }`}
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div
          className="bg-rose-500/10 border border-rose-500/30 rounded-lg px-4 py-3 text-sm text-rose-300 flex items-start gap-2"
          data-testid="code-evo-error"
        >
          <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
          <div>{error}</div>
        </div>
      )}

      {toast && (
        <div
          className={`rounded-lg px-4 py-3 text-sm flex items-start gap-2 border ${
            toast.type === 'ok'
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
              : 'bg-amber-500/10 border-amber-500/30 text-amber-300'
          }`}
          data-testid="code-evo-toast"
        >
          <CheckCircle2 size={16} className="flex-shrink-0 mt-0.5" />
          <div>{toast.text}</div>
        </div>
      )}

      {!loading && receipts.length === 0 && (
        <div
          className="bg-slate-900/40 border border-slate-800 rounded-lg p-8 text-center"
          data-testid="code-evo-empty"
        >
          <FileWarning size={28} className="mx-auto text-slate-600 mb-2" />
          <div className="text-sm text-slate-400">
            No receipts yet. Submit a patch via{' '}
            <code className="text-cyan-400 font-mono text-xs">
              POST /api/admin/code-evolution/evaluate
            </code>{' '}to see it appear here.
          </div>
        </div>
      )}

      {receipts.length > 0 && (
        <>
          {/* Aggregate counts */}
          <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
            <span className="text-slate-500 uppercase tracking-widest mr-1">
              {receipts.length} receipts
            </span>
            {Object.entries(counts).map(([status, n]) => (
              <span
                key={status}
                className="inline-flex items-center gap-1"
              >
                <StatusPill status={status} />
                <span className="text-slate-400">×{n}</span>
              </span>
            ))}
          </div>

          {/* Receipt table */}
          <div className="bg-slate-900/40 border border-slate-800 rounded-lg overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="bg-slate-900/60">
                  <th className="text-left text-[10px] uppercase tracking-widest text-slate-500 font-mono py-2 px-3">
                    Patch
                  </th>
                  <th className="text-left text-[10px] uppercase tracking-widest text-slate-500 font-mono py-2 px-3">
                    Status / Risk
                  </th>
                  <th className="text-left text-[10px] uppercase tracking-widest text-slate-500 font-mono py-2 px-3">
                    Signatures
                  </th>
                  <th className="text-left text-[10px] uppercase tracking-widest text-slate-500 font-mono py-2 px-3">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody>
                {receipts.map((r) => (
                  <ReceiptRow
                    key={r.patch_id}
                    receipt={r}
                    onApprove={onApprove}
                    onReject={onReject}
                    busyId={busyId}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="text-xs text-slate-600 pt-2 border-t border-slate-800/40 flex items-center gap-2">
        <ShieldCheck size={12} className="text-slate-700" />
        <span>
          Doctrine: <code className="text-slate-400 font-mono">may_auto_promote() === false</code> · receipts mirror the gate, never override it.
        </span>
      </div>
    </div>
  );
}
