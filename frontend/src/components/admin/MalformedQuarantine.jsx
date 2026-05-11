import React from 'react';
import {
  AlertOctagon, RefreshCw, ChevronDown, ChevronRight, CheckCircle2,
  FileWarning, ArrowUpCircle, X,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * MalformedQuarantine — Shelly Doctrine v2 operator panel for the
 * ``shelly_legacy_malformed`` quarantine bin.
 *
 * Doctrine (2026-05-12):
 *   "If malformed it still must be labeled legacy, date, time and
 *    ID. Place malformed in a file of its own, numbered by the
 *    number of documents in file."
 *
 * Promote action re-submits the (optionally edited) payload through
 * /api/admin/shelly-memory/malformed/{doc_number}/promote — on
 * memory-lane success, the malformed row is stamped with a link to
 * the new memory_id. The original row is never deleted (audit
 * trail is permanent and numbered).
 */
export default function MalformedQuarantine() {
  const [rows, setRows] = React.useState([]);
  const [count, setCount] = React.useState(0);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [minDocNumber, setMinDocNumber] = React.useState('');
  const [expanded, setExpanded] = React.useState(new Set());
  const [promote, setPromote] = React.useState(null); // {row, payload_text, source}

  const fetchRows = React.useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const qs = minDocNumber ? `&min_doc_number=${encodeURIComponent(minDocNumber)}` : '';
      const res = await authFetch(`${API}/admin/shelly-memory/malformed?limit=200${qs}`);
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || 'fetch failed');
      setRows(j.rows || []);
      setCount(j.count || 0);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [minDocNumber]);

  React.useEffect(() => { fetchRows(); }, [fetchRows]);

  const toggleExpand = (docNumber) => {
    const next = new Set(expanded);
    if (next.has(docNumber)) next.delete(docNumber); else next.add(docNumber);
    setExpanded(next);
  };

  const openPromote = (row) => {
    setPromote({
      row,
      payload_text: JSON.stringify(row.raw_payload, null, 2),
      source: row.source || '',
    });
  };

  const submitPromote = async () => {
    if (!promote) return;
    setBusy(true);
    setError(null);
    try {
      let corrected_payload;
      try {
        corrected_payload = JSON.parse(promote.payload_text);
      } catch (e) {
        // Fall back to raw string — perceive() handles both shapes.
        corrected_payload = promote.payload_text;
      }
      const res = await authFetch(
        `${API}/admin/shelly-memory/malformed/${promote.row.doc_number}/promote`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            corrected_payload,
            source: promote.source || undefined,
          }),
        },
      );
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || 'promote failed');
      setPromote(null);
      await fetchRows();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const promotedCount = rows.filter((r) => r.promoted_to_memory_id).length;
  const pendingCount = rows.length - promotedCount;

  return (
    <div className="p-4 sm:p-6 space-y-4" data-testid="malformed-quarantine-panel">
      {/* ── Header ── */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 bg-amber-500/15 border border-amber-500/40 rounded-xl flex items-center justify-center shrink-0">
            <FileWarning className="w-5 h-5 text-amber-300" />
          </div>
          <div className="min-w-0">
            <h3 className="text-white text-base font-bold">Malformed Quarantine</h3>
            <p className="text-slate-400 text-xs">
              <span className="font-mono text-amber-300">shelly_legacy_malformed</span> · numbered audit trail · permanent
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2 text-xs">
            <span className="px-2 py-1 rounded-md bg-amber-500/15 border border-amber-500/30 text-amber-200">
              {pendingCount} pending
            </span>
            <span className="px-2 py-1 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-emerald-200">
              {promotedCount} promoted
            </span>
          </div>
          <button
            type="button"
            onClick={fetchRows}
            disabled={busy}
            className="p-2 rounded-lg bg-slate-800/70 hover:bg-slate-700/80 text-slate-200 border border-slate-700 disabled:opacity-50"
            data-testid="malformed-refresh-btn"
            aria-label="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${busy ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* ── Filter bar ── */}
      <div className="flex items-center gap-2">
        <label className="text-xs text-slate-400">From doc #</label>
        <input
          type="number"
          min="1"
          value={minDocNumber}
          onChange={(e) => setMinDocNumber(e.target.value)}
          placeholder="all"
          className="bg-slate-900 border border-slate-700 rounded-md px-2 py-1 text-sm text-slate-100 w-28 focus:outline-none focus:border-slate-500"
          data-testid="malformed-min-doc-input"
        />
        {minDocNumber && (
          <button
            type="button"
            onClick={() => setMinDocNumber('')}
            className="text-xs text-slate-400 hover:text-slate-200 underline"
            data-testid="malformed-clear-filter-btn"
          >
            clear
          </button>
        )}
        <span className="text-xs text-slate-500 ml-auto">
          Showing {rows.length} of {count}
        </span>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-md bg-red-500/10 border border-red-500/40 text-red-200 text-xs flex items-center gap-2" data-testid="malformed-error">
          <AlertOctagon className="w-4 h-4" />
          {error}
        </div>
      )}

      {/* ── Rows ── */}
      {rows.length === 0 && !busy && (
        <div className="text-center py-12 text-slate-500 text-sm" data-testid="malformed-empty">
          <CheckCircle2 className="w-8 h-8 mx-auto mb-2 text-emerald-400/60" />
          The quarantine bin is empty. Shelly hasn't routed anything to legacy.
        </div>
      )}

      <div className="space-y-2">
        {rows.map((row) => {
          const isOpen = expanded.has(row.doc_number);
          const promoted = Boolean(row.promoted_to_memory_id);
          return (
            <div
              key={row.doc_number}
              className={`rounded-lg border ${promoted ? 'border-emerald-700/40 bg-emerald-900/10' : 'border-amber-700/40 bg-amber-900/5'}`}
              data-testid={`malformed-row-${row.doc_number}`}
            >
              <button
                type="button"
                onClick={() => toggleExpand(row.doc_number)}
                className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-slate-800/40 transition-colors"
                data-testid={`malformed-toggle-${row.doc_number}`}
              >
                {isOpen ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                <span className="font-mono text-xs text-slate-300 shrink-0">#{row.doc_number}</span>
                <span className="text-xs text-slate-400 shrink-0">{row.legacy_date}</span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300 shrink-0">
                  {row.source}
                </span>
                <span className="text-xs text-amber-300/80 truncate flex-1 min-w-0" title={row.error}>
                  {row.error}
                </span>
                {promoted && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/40 text-emerald-200 shrink-0 flex items-center gap-1">
                    <CheckCircle2 className="w-3 h-3" /> promoted
                  </span>
                )}
              </button>

              {isOpen && (
                <div className="px-3 pb-3 pt-1 space-y-2 border-t border-slate-800/60">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                    <Field label="legacy_id" value={row.legacy_id} mono />
                    <Field label="legacy_time" value={row.legacy_time} mono />
                    <Field label="embedding_version" value={row.embedding_version} />
                    <Field label="created_at" value={row.created_at} mono />
                    {promoted && (
                      <>
                        <Field label="promoted_to" value={row.promoted_to_memory_id} mono />
                        <Field label="promoted_at" value={row.promoted_at} mono />
                      </>
                    )}
                  </div>
                  <div>
                    <div className="text-xs text-slate-400 mb-1">raw_payload</div>
                    <pre className="text-xs bg-slate-950/80 border border-slate-800 rounded-md p-2 overflow-x-auto max-h-48 text-slate-200" data-testid={`malformed-raw-${row.doc_number}`}>
{JSON.stringify(row.raw_payload, null, 2)}
                    </pre>
                  </div>
                  {!promoted && (
                    <div className="flex justify-end">
                      <button
                        type="button"
                        onClick={() => openPromote(row)}
                        className="px-3 py-1.5 rounded-md bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/40 text-amber-200 text-xs font-medium flex items-center gap-1.5"
                        data-testid={`malformed-promote-btn-${row.doc_number}`}
                      >
                        <ArrowUpCircle className="w-3.5 h-3.5" />
                        Promote to memory
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ── Promote modal ── */}
      {promote && (
        <PromoteModal
          row={promote.row}
          payloadText={promote.payload_text}
          source={promote.source}
          busy={busy}
          onChangePayload={(v) => setPromote({ ...promote, payload_text: v })}
          onChangeSource={(v) => setPromote({ ...promote, source: v })}
          onCancel={() => setPromote(null)}
          onSubmit={submitPromote}
        />
      )}
    </div>
  );
}

function Field({ label, value, mono }) {
  return (
    <div>
      <div className="text-slate-500 text-[10px] uppercase tracking-wide">{label}</div>
      <div className={`text-slate-200 truncate ${mono ? 'font-mono text-[11px]' : ''}`} title={String(value ?? '')}>
        {value ?? '—'}
      </div>
    </div>
  );
}

function PromoteModal({ row, payloadText, source, busy, onChangePayload, onChangeSource, onCancel, onSubmit }) {
  return (
    <div className="fixed inset-0 bg-black/70 z-[60] flex items-center justify-center p-4" data-testid="malformed-promote-modal">
      <div className="bg-slate-900 border border-slate-700 rounded-xl max-w-2xl w-full my-4 max-h-[90vh] flex flex-col">
        <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ArrowUpCircle className="w-5 h-5 text-amber-300" />
            <h4 className="text-white font-bold">Promote malformed #{row.doc_number} → memory</h4>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 disabled:opacity-50"
            data-testid="malformed-promote-close-btn"
            aria-label="Cancel"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-4 space-y-3 overflow-y-auto">
          <div className="text-xs text-slate-400">
            Edit the payload below to fix what caused quarantine
            (e.g. supply a valid <span className="font-mono text-amber-300">event_date</span> like
            <span className="font-mono text-amber-300"> 2024-03-15</span>),
            then submit. The malformed row stays in the audit
            trail; a new doctrine-stamped record lands in
            <span className="font-mono text-emerald-300"> shelly_memories</span>.
          </div>
          <div>
            <label className="text-xs text-slate-400 block mb-1">source</label>
            <input
              type="text"
              value={source}
              onChange={(e) => onChangeSource(e.target.value)}
              className="w-full bg-slate-950 border border-slate-700 rounded-md px-2 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-slate-500"
              data-testid="malformed-promote-source-input"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400 block mb-1">corrected_payload (JSON or plain text)</label>
            <textarea
              value={payloadText}
              onChange={(e) => onChangePayload(e.target.value)}
              rows={12}
              className="w-full bg-slate-950 border border-slate-700 rounded-md px-2 py-1.5 text-xs font-mono text-slate-100 focus:outline-none focus:border-slate-500 resize-y"
              data-testid="malformed-promote-payload-input"
            />
          </div>
          <div className="text-xs text-amber-300/80 px-3 py-2 rounded-md bg-amber-500/10 border border-amber-500/30">
            <strong>Doctrine:</strong> if the corrected payload still fails the stamper,
            a NEW malformed row is created with its own doc_number. The original row
            is never deleted.
          </div>
        </div>
        <div className="px-4 py-3 border-t border-slate-700 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="px-3 py-1.5 rounded-md bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-sm disabled:opacity-50"
            data-testid="malformed-promote-cancel-btn"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSubmit}
            disabled={busy}
            className="px-3 py-1.5 rounded-md bg-amber-500 hover:bg-amber-400 text-amber-950 text-sm font-bold disabled:opacity-50 flex items-center gap-1.5"
            data-testid="malformed-promote-submit-btn"
          >
            <ArrowUpCircle className="w-4 h-4" />
            {busy ? 'Promoting…' : 'Submit'}
          </button>
        </div>
      </div>
    </div>
  );
}
