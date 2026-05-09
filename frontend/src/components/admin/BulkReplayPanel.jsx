import React from 'react';
import { Upload, FileWarning, ShieldCheck, AlertTriangle, Activity } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * BulkReplayPanel — pre-ingest CSV scanner.
 *
 * Operator drag-drops a historical paper-trade CSV. Every row is
 * passed through the existing Chevelle Memory Labeling Firewall.
 * Returns per-row verdicts + an aggregate summary so the operator
 * can review before any future ingest pipeline is built.
 *
 * Hard rules surfaced in this UI:
 *   - No "import into memory" button.
 *   - No "train all" button.
 *   - No label override.
 *   - The tile copy explicitly states the read-only nature.
 *
 * The endpoint enforces a 500-row cap and a 2 MiB file-size cap.
 * Both caps are surfaced in the UI footer so the operator knows
 * the limits without reading the docs.
 */
const GRADE_STYLES = {
  GREEN: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  AMBER: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  RED:   'bg-rose-500/10 text-rose-400 border-rose-500/30',
};

function StatTile({ label, value, accent = 'cyan', testid }) {
  const accentMap = {
    cyan:    'text-cyan-400',
    emerald: 'text-emerald-400',
    amber:   'text-amber-400',
    rose:    'text-rose-400',
    slate:   'text-slate-300',
  };
  return (
    <div
      data-testid={testid}
      className="bg-slate-900/60 border border-slate-800 rounded-lg p-4"
    >
      <div className="text-[11px] uppercase tracking-widest text-slate-500 mb-1">
        {label}
      </div>
      <div className={`text-2xl font-semibold ${accentMap[accent] || ''}`}>
        {value}
      </div>
    </div>
  );
}

function BreakdownTable({ title, data, testid }) {
  const entries = Object.entries(data || {});
  if (entries.length === 0) {
    return null;
  }
  // Sort by count desc.
  entries.sort((a, b) => b[1] - a[1]);
  return (
    <div
      data-testid={testid}
      className="bg-slate-900/40 border border-slate-800 rounded-lg p-4"
    >
      <div className="text-xs uppercase tracking-widest text-slate-500 mb-3">
        {title}
      </div>
      <div className="space-y-1 text-sm">
        {entries.map(([k, v]) => (
          <div key={k} className="flex justify-between text-slate-300">
            <span className="font-mono text-xs text-slate-400">{k}</span>
            <span className="font-semibold">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function BulkReplayPanel() {
  const [file, setFile] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [report, setReport] = React.useState(null);
  const [dragActive, setDragActive] = React.useState(false);
  const [filter, setFilter] = React.useState('all'); // all | green | amber | red

  const onSelectFile = (f) => {
    if (!f) return;
    setFile(f);
    setReport(null);
    setError(null);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      onSelectFile(e.dataTransfer.files[0]);
    }
  };

  const onScan = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await authFetch(
        `${API}/admin/bulk-replay/scan`,
        { method: 'POST', body: fd },
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`scan failed (${res.status}): ${text}`);
      }
      const json = await res.json();
      setReport(json);
    } catch (e) {
      setError(e.message || 'unknown error');
    } finally {
      setLoading(false);
    }
  };

  const filteredRows = React.useMemo(() => {
    if (!report?.rows) return [];
    if (filter === 'all') return report.rows;
    return report.rows.filter((r) => r.grade === filter.toUpperCase());
  }, [report, filter]);

  return (
    <div data-testid="bulk-replay-panel" className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-100 mb-1">
            Bulk Replay
          </h2>
          <p className="text-sm text-slate-400 max-w-2xl">
            Pre-ingest firewall sanity check for historical paper-trade CSVs.
            Every row passes through the Chevelle Memory Labeling Firewall.
            <span className="text-amber-400"> Read-only — no DB writes, no training, no promotion.</span>
          </p>
        </div>
      </div>

      {/* Drop zone */}
      <div
        data-testid="bulk-replay-dropzone"
        onDragEnter={(e) => { e.preventDefault(); setDragActive(true); }}
        onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
        onDragLeave={() => setDragActive(false)}
        onDrop={onDrop}
        className={`border-2 border-dashed rounded-xl p-10 text-center transition-colors ${
          dragActive ? 'border-cyan-400 bg-cyan-500/5'
                     : 'border-slate-700 bg-slate-900/30'
        }`}
      >
        <Upload className="mx-auto mb-3 text-slate-500" size={32} />
        <div className="text-sm text-slate-400 mb-3">
          Drag a CSV file here, or
          <label className="ml-1 text-cyan-400 cursor-pointer hover:underline">
            <input
              data-testid="bulk-replay-file-input"
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(e) => onSelectFile(e.target.files?.[0])}
            />
            choose a file
          </label>
        </div>
        {file && (
          <div className="text-xs text-slate-500 mb-3 font-mono">
            {file.name} · {(file.size / 1024).toFixed(1)} KB
          </div>
        )}
        <button
          type="button"
          data-testid="bulk-replay-scan-btn"
          disabled={!file || loading}
          onClick={onScan}
          className={`px-5 py-2 rounded-md text-sm font-semibold transition-colors ${
            !file || loading
              ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
              : 'bg-cyan-500 hover:bg-cyan-400 text-slate-950'
          }`}
        >
          {loading ? 'Scanning...' : 'Scan'}
        </button>
        <div className="text-[11px] uppercase tracking-widest text-slate-600 mt-4">
          Max 500 rows · Max 2 MB · Owner-only · No DB writes
        </div>
      </div>

      {/* Error */}
      {error && (
        <div
          data-testid="bulk-replay-error"
          className="bg-rose-500/10 border border-rose-500/30 rounded-lg px-4 py-3 text-sm text-rose-300 flex items-start gap-2"
        >
          <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
          <div>{error}</div>
        </div>
      )}

      {/* Report */}
      {report && (
        <div className="space-y-6">
          {/* Aggregate stats */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            <StatTile
              testid="agg-total"
              label="Total rows"
              value={report.aggregate.total_rows}
              accent="slate"
            />
            <StatTile
              testid="agg-trainable"
              label="Trainable"
              value={report.aggregate.trainable_count}
              accent="emerald"
            />
            <StatTile
              testid="agg-toxic"
              label="Toxic"
              value={report.aggregate.toxic_count}
              accent="amber"
            />
            <StatTile
              testid="agg-quarantined"
              label="Quarantined"
              value={report.aggregate.quarantined_count}
              accent="rose"
            />
            <StatTile
              testid="agg-avg-trust"
              label="Avg trust"
              value={(report.aggregate.avg_trust_weight ?? 0).toFixed(3)}
              accent="cyan"
            />
          </div>

          {/* Warnings */}
          {report.warnings?.length > 0 && (
            <div
              data-testid="bulk-replay-warnings"
              className="bg-amber-500/5 border border-amber-500/30 rounded-lg p-4"
            >
              <div className="flex items-center gap-2 text-amber-400 text-xs uppercase tracking-widest mb-2">
                <FileWarning size={14} />
                <span>Warnings</span>
              </div>
              <ul className="text-sm text-slate-300 space-y-1 list-disc list-inside">
                {report.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Breakdowns */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <BreakdownTable
              testid="breakdown-source"
              title="By source"
              data={report.aggregate.by_source}
            />
            <BreakdownTable
              testid="breakdown-lane"
              title="By lane"
              data={report.aggregate.by_lane}
            />
            <BreakdownTable
              testid="breakdown-event-era"
              title="By event era"
              data={report.aggregate.by_event_era}
            />
            <BreakdownTable
              testid="breakdown-failure-mode"
              title="By failure mode"
              data={report.aggregate.by_failure_mode}
            />
            <BreakdownTable
              testid="breakdown-data-quality"
              title="By data quality"
              data={report.aggregate.by_data_quality}
            />
          </div>

          {/* Filter chips */}
          <div className="flex items-center gap-2 text-xs">
            <Activity size={14} className="text-slate-500" />
            <span className="text-slate-500 uppercase tracking-widest">Filter:</span>
            {['all', 'green', 'amber', 'red'].map((f) => (
              <button
                key={f}
                type="button"
                data-testid={`filter-${f}`}
                onClick={() => setFilter(f)}
                className={`px-2.5 py-1 rounded-full font-mono uppercase tracking-wider ${
                  filter === f
                    ? 'bg-cyan-500 text-slate-950'
                    : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                }`}
              >
                {f}
              </button>
            ))}
            <span className="text-slate-500 ml-2">
              {filteredRows.length} / {report.rows.length} rows
            </span>
          </div>

          {/* Per-row table */}
          <div className="overflow-x-auto border border-slate-800 rounded-lg">
            <table className="w-full text-sm" data-testid="bulk-replay-row-table">
              <thead className="bg-slate-900/80 text-xs uppercase tracking-widest text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left">Row</th>
                  <th className="px-3 py-2 text-left">Symbol</th>
                  <th className="px-3 py-2 text-left">Lane</th>
                  <th className="px-3 py-2 text-left">Source</th>
                  <th className="px-3 py-2 text-left">Era</th>
                  <th className="px-3 py-2 text-left">Failure</th>
                  <th className="px-3 py-2 text-right">Trust</th>
                  <th className="px-3 py-2 text-center">Grade</th>
                  <th className="px-3 py-2 text-left">Reason / Trace</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row, idx) => (
                  <tr
                    key={idx}
                    className="border-t border-slate-800/50 hover:bg-slate-900/40"
                  >
                    <td className="px-3 py-2 font-mono text-xs text-slate-500">
                      {row.csv_row_index}
                    </td>
                    <td className="px-3 py-2 font-mono text-cyan-400">
                      {row.symbol || '—'}
                    </td>
                    <td className="px-3 py-2 text-slate-300">{row.lane}</td>
                    <td className="px-3 py-2 text-slate-300">{row.source}</td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-400">
                      {row.event_era}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-400">
                      {row.failure_mode}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-200">
                      {row.trust_weight.toFixed(2)}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <span
                        className={`inline-block px-2 py-0.5 text-[10px] rounded border ${GRADE_STYLES[row.grade]}`}
                      >
                        {row.grade}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      {row.rejection_reason || row.rule_trace.join(', ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredRows.length === 0 && (
              <div className="p-6 text-center text-slate-500 text-sm">
                No rows match the current filter.
              </div>
            )}
          </div>

          {/* Footer note */}
          <div className="text-xs text-slate-600 flex items-center gap-2 pt-2 border-t border-slate-800/40">
            <ShieldCheck size={12} />
            <span>
              Read-only firewall scan. Nothing has been written to memory or queued for training.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
