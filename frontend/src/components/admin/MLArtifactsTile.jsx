/**
 * MLArtifactsTile — read-only ML artifact inventory.
 *
 * GET /api/admin/ml/artifacts → sortable table.
 *
 * Hard contract (matches backend invariants):
 *   * NO promote button
 *   * NO set-active button
 *   * NO env editing
 *   * NO write endpoints called
 *   * NO joblib loading anywhere on the page
 *
 * Visibility cues (read-only):
 *   * green dot when currently_pointed_to_by_env=true
 *   * red "STALE" badge when env-pointed artifact has age_hours > 72
 *   * yellow "NO MANIFEST" badge when manifest_present=false
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import {
  Boxes, RefreshCw, CheckCircle2, AlertTriangle, FileWarning,
  ArrowUpDown,
} from 'lucide-react';

const API = `${getApiBase()}/api`;
const REFRESH_INTERVAL_MS = 60_000;
const STALE_AGE_HOURS = 72;

const COLUMNS = [
  { key: 'model_type',                label: 'Type' },
  { key: 'filename',                  label: 'Filename' },
  { key: 'sha_from_filename',         label: 'SHA' },
  { key: 'timestamp_from_filename',   label: 'Timestamp' },
  { key: 'size_bytes',                label: 'Size' },
  { key: 'age_hours',                 label: 'Age' },
  { key: 'manifest_present',          label: 'Manifest' },
  { key: 'currently_pointed_to_by_env', label: 'Active' },
];

const formatSize = (bytes) => {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const formatAge = (hours) => {
  if (hours === null || hours === undefined) return '—';
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 48) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
};

const formatTs = (raw) => {
  if (!raw) return '—';
  // Prefer the embedded UTC stamp from the filename when present.
  const m = /^(\d{8})T(\d{6})Z$/.exec(raw);
  if (m) {
    const d = m[1];
    const t = m[2];
    return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)} ${t.slice(0, 2)}:${t.slice(2, 4)}`;
  }
  return raw;
};

const TypeBadge = ({ type }) => {
  const styles = {
    strategist: 'bg-cyan-950/60 border-cyan-800 text-cyan-200',
    auditor:    'bg-violet-950/60 border-violet-800 text-violet-200',
    unknown:    'bg-slate-900/60 border-slate-800 text-slate-400',
  };
  const cls = styles[type] || styles.unknown;
  return (
    <span
      data-testid={`artifact-type-${type}`}
      className={`inline-flex px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider border ${cls}`}
    >
      {type || 'unknown'}
    </span>
  );
};

const ActiveDot = ({ active }) => (
  <span
    data-testid={active ? 'artifact-active-yes' : 'artifact-active-no'}
    className={`inline-flex items-center gap-1.5 ${active ? 'text-emerald-300' : 'text-slate-600'}`}
  >
    <span
      className={`w-2 h-2 rounded-full ${active ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.7)]' : 'bg-slate-700'}`}
    />
    {active ? 'active' : '—'}
  </span>
);

const StaleBadge = () => (
  <span
    data-testid="artifact-stale-badge"
    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-rose-950/60 border border-rose-800 text-rose-200 text-[10px] uppercase tracking-wider"
  >
    <AlertTriangle className="w-2.5 h-2.5" /> stale
  </span>
);

const NoManifestBadge = () => (
  <span
    data-testid="artifact-no-manifest-badge"
    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-950/60 border border-amber-800 text-amber-200 text-[10px] uppercase tracking-wider"
  >
    <FileWarning className="w-2.5 h-2.5" /> no manifest
  </span>
);

const ManifestCell = ({ present }) =>
  present ? (
    <span className="inline-flex items-center gap-1 text-slate-300" data-testid="artifact-manifest-yes">
      <CheckCircle2 className="w-3 h-3 text-emerald-400" /> yes
    </span>
  ) : (
    <NoManifestBadge />
  );

const SortableHeader = ({ col, sortKey, sortDir, onSort }) => {
  const active = sortKey === col.key;
  return (
    <th
      data-testid={`artifact-col-${col.key}`}
      className={`px-3 py-2 text-left text-[10px] uppercase tracking-wider cursor-pointer select-none transition-colors ${
        active ? 'text-slate-100' : 'text-slate-500 hover:text-slate-300'
      }`}
      onClick={() => onSort(col.key)}
    >
      <span className="inline-flex items-center gap-1">
        {col.label}
        <ArrowUpDown className={`w-3 h-3 ${active ? 'opacity-100' : 'opacity-30'}`} />
        {active && (
          <span className="text-[8px] font-mono">
            {sortDir === 'desc' ? '↓' : '↑'}
          </span>
        )}
      </span>
    </th>
  );
};

const compare = (a, b, key, dir) => {
  const av = a?.[key];
  const bv = b?.[key];
  // Nulls last regardless of direction.
  if (av === null || av === undefined) return 1;
  if (bv === null || bv === undefined) return -1;
  let cmp;
  if (typeof av === 'number' && typeof bv === 'number') {
    cmp = av - bv;
  } else if (typeof av === 'boolean' && typeof bv === 'boolean') {
    cmp = av === bv ? 0 : av ? -1 : 1;
  } else {
    cmp = String(av).localeCompare(String(bv));
  }
  return dir === 'desc' ? -cmp : cmp;
};

const MLArtifactsTile = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sortKey, setSortKey] = useState('mtime');
  const [sortDir, setSortDir] = useState('desc');
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const intervalRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const resp = await authFetch(`${API}/admin/ml/artifacts`);
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 160)}`);
      }
      setData(await resp.json());
      setError(null);
      setLastRefreshed(new Date());
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    intervalRef.current = setInterval(load, REFRESH_INTERVAL_MS);
    return () => intervalRef.current && clearInterval(intervalRef.current);
  }, [load]);

  const onSort = useCallback((key) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
        return prev;
      }
      setSortDir('desc');
      return key;
    });
  }, []);

  const sortedItems = useMemo(() => {
    const items = data?.items || [];
    return [...items].sort((a, b) => compare(a, b, sortKey, sortDir));
  }, [data, sortKey, sortDir]);

  const activeCount = useMemo(
    () => (data?.items || []).filter((i) => i.currently_pointed_to_by_env).length,
    [data],
  );
  const staleActive = useMemo(
    () => (data?.items || []).some(
      (i) => i.currently_pointed_to_by_env && (i.age_hours || 0) > STALE_AGE_HOURS,
    ),
    [data],
  );

  return (
    <div
      className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden"
      data-testid="ml-artifacts-tile"
    >
      <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-md bg-slate-900 border border-slate-800 flex items-center justify-center">
            <Boxes className={`w-4 h-4 ${staleActive ? 'text-rose-400' : 'text-slate-300'}`} />
          </div>
          <div>
            <h3 className="text-base font-semibold text-slate-100 leading-tight">
              ML Artifacts
            </h3>
            <p className="text-[11px] text-slate-500 leading-tight mt-0.5">
              Read-only · GET /api/admin/ml/artifacts · refresh 60s · {data?.count ?? 0} files · {activeCount} active
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {staleActive && (
            <span
              data-testid="artifacts-banner-stale-active"
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-950/60 border border-rose-800 text-rose-200 text-[10px] uppercase tracking-wide"
            >
              <AlertTriangle className="w-3 h-3" /> stale active artifact
            </span>
          )}
          <button
            onClick={load}
            data-testid="artifacts-refresh-btn"
            className="p-1.5 rounded-md bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 transition-colors"
            title="Refresh now"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      <div className="p-5">
        {error && (
          <div
            className="bg-rose-950/40 border border-rose-900 text-rose-200 rounded-md p-3 text-xs mb-3"
            data-testid="artifacts-error"
          >
            Failed to load artifacts: {error}
          </div>
        )}

        {loading && !data && (
          <div className="text-sm text-slate-400 px-4 py-6 text-center" data-testid="artifacts-loading">
            Loading…
          </div>
        )}

        {data && sortedItems.length === 0 && (
          <div
            className="text-sm text-slate-400 px-4 py-8 text-center border border-dashed border-slate-800 rounded-md"
            data-testid="artifacts-empty-state"
          >
            No model artifacts found.
            <div className="text-[10px] text-slate-600 mt-1 font-mono">
              {data.models_dir}
            </div>
          </div>
        )}

        {data && sortedItems.length > 0 && (
          <div className="overflow-x-auto">
            <table
              className="w-full text-xs"
              data-testid="artifacts-table"
            >
              <thead>
                <tr className="border-b border-slate-800/80 bg-slate-900/40">
                  {COLUMNS.map((col) => (
                    <SortableHeader
                      key={col.key}
                      col={col}
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={onSort}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((item) => {
                  const isStale =
                    item.currently_pointed_to_by_env && (item.age_hours || 0) > STALE_AGE_HOURS;
                  return (
                    <tr
                      key={item.full_path}
                      data-testid={`artifact-row-${item.filename}`}
                      className={`border-b border-slate-800/40 hover:bg-slate-800/30 transition-colors ${
                        item.currently_pointed_to_by_env ? 'bg-emerald-950/15' : ''
                      }`}
                    >
                      <td className="px-3 py-2"><TypeBadge type={item.model_type} /></td>
                      <td className="px-3 py-2 font-mono text-slate-300 truncate max-w-[320px]" title={item.full_path}>
                        {item.filename}
                      </td>
                      <td className="px-3 py-2 font-mono text-slate-400">
                        {item.sha_from_filename || '—'}
                      </td>
                      <td className="px-3 py-2 font-mono text-slate-400">
                        {formatTs(item.timestamp_from_filename)}
                      </td>
                      <td className="px-3 py-2 font-mono text-slate-400">
                        {formatSize(item.size_bytes)}
                      </td>
                      <td className="px-3 py-2 font-mono">
                        <span className={isStale ? 'text-rose-300' : 'text-slate-400'}>
                          {formatAge(item.age_hours)}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <ManifestCell present={item.manifest_present} />
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <ActiveDot active={item.currently_pointed_to_by_env} />
                          {isStale && <StaleBadge />}
                          {item.env_var && (
                            <span className="text-[9px] font-mono text-emerald-400/80">
                              {item.env_var}
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-3 flex items-center justify-between text-[10px] text-slate-600 font-mono">
          <span data-testid="artifacts-models-dir">
            models_dir: {data?.models_dir || '—'}
          </span>
          <span>
            {lastRefreshed ? `refreshed ${lastRefreshed.toLocaleTimeString()}` : ''}
          </span>
        </div>
      </div>
    </div>
  );
};

export default MLArtifactsTile;
