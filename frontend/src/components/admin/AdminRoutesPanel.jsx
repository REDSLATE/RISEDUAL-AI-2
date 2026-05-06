import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  GitBranch, AlertTriangle, ShieldCheck, RefreshCw, Search, Lock, Globe, UserCheck,
} from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Admin Routes Diagnostic Panel.
 *
 * Reads the read-only ``GET /api/admin/_routes`` introspection
 * endpoint and surfaces:
 *
 *   * Total count, per-module breakdown, gate distribution.
 *   * **Duplicates** — any (method, path) pair registered twice
 *     by the FastAPI app. Empty list = healthy decomposition.
 *     Non-empty = an extraction left a stub behind, which is
 *     the exact bug class this panel was built to catch.
 *   * Filterable, searchable list of every route with module +
 *     gate level (owner / admin / auth / open / unknown).
 *
 * No writes, no orchestration — the panel is purely a window
 * into the FastAPI app's route registry.
 */

const GATE_CONFIG = {
  owner: { label: 'OWNER', Icon: Lock, cls: 'bg-rose-500/15 text-rose-300 border-rose-500/30' },
  admin: { label: 'ADMIN', Icon: ShieldCheck, cls: 'bg-amber-500/15 text-amber-300 border-amber-500/30' },
  auth: { label: 'AUTH', Icon: UserCheck, cls: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30' },
  open: { label: 'OPEN', Icon: Globe, cls: 'bg-slate-500/15 text-slate-300 border-slate-500/30' },
  unknown: { label: '?', Icon: AlertTriangle, cls: 'bg-slate-700/30 text-slate-400 border-slate-600/40' },
};

const METHOD_CLS = {
  GET: 'text-cyan-300',
  POST: 'text-emerald-300',
  PUT: 'text-amber-300',
  PATCH: 'text-amber-300',
  DELETE: 'text-rose-300',
};

const AdminRoutesPanel = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [moduleFilter, setModuleFilter] = useState('');
  const [gateFilter, setGateFilter] = useState('');
  const [search, setSearch] = useState('');

  const fetchRoutes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(`${API}/admin/_routes`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      logger.error('admin routes fetch failed:', e);
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchRoutes(); }, [fetchRoutes]);

  const filteredRoutes = useMemo(() => {
    if (!data?.routes) return [];
    const q = search.trim().toLowerCase();
    return data.routes.filter(r => {
      if (moduleFilter && r.module !== moduleFilter) return false;
      if (gateFilter && r.gate !== gateFilter) return false;
      if (q && !r.path.toLowerCase().includes(q) && !r.name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data, moduleFilter, gateFilter, search]);

  const dupCount = data?.duplicates?.length ?? 0;

  return (
    <div className="p-6 space-y-5" data-testid="admin-routes-panel">
      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="admin-routes-total">
          <div className="flex items-center gap-2 mb-2">
            <GitBranch className="w-4 h-4 text-[#3DE8D9]" />
            <span className="text-slate-300 text-xs">Total Routes</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : (data?.total ?? 0).toLocaleString()}
          </p>
          <p className="text-[10px] text-slate-500 mt-1">/api/admin/*</p>
        </Card>

        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="admin-routes-modules">
          <div className="flex items-center gap-2 mb-2">
            <ShieldCheck className="w-4 h-4 text-cyan-400" />
            <span className="text-slate-300 text-xs">Source Modules</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : Object.keys(data?.by_module ?? {}).length}
          </p>
          <p className="text-[10px] text-slate-500 mt-1">decomposed</p>
        </Card>

        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="admin-routes-owner-gated">
          <div className="flex items-center gap-2 mb-2">
            <Lock className="w-4 h-4 text-rose-300" />
            <span className="text-slate-300 text-xs">Owner-Gated</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : (data?.by_gate?.owner ?? 0)}
          </p>
          <p className="text-[10px] text-slate-500 mt-1">strictest tier</p>
        </Card>

        <Card
          className={`border rounded-xl p-4 ${
            dupCount > 0
              ? 'bg-rose-900/30 border-rose-500/40'
              : 'bg-emerald-900/20 border-emerald-500/30'
          }`}
          data-testid="admin-routes-duplicates"
        >
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className={`w-4 h-4 ${dupCount > 0 ? 'text-rose-300' : 'text-emerald-300'}`} />
            <span className="text-slate-300 text-xs">Duplicates</span>
          </div>
          <p className={`text-2xl font-black ${dupCount > 0 ? 'text-rose-300' : 'text-emerald-300'}`}>
            {loading ? '—' : dupCount}
          </p>
          <p className="text-[10px] text-slate-500 mt-1">
            {dupCount > 0 ? 'ACTION NEEDED' : 'all unique'}
          </p>
        </Card>
      </div>

      {/* Duplicates callout */}
      {dupCount > 0 && (
        <Card className="bg-rose-950/40 border-rose-500/40 rounded-xl p-4" data-testid="admin-routes-duplicates-list">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-4 h-4 text-rose-300" />
            <span className="text-rose-300 text-sm font-semibold">
              Duplicate route registrations detected
            </span>
          </div>
          <p className="text-xs text-slate-400 mb-3">
            Each row is a (method, path) registered more than once.
            Likely an extraction left a stub behind — investigate
            the originating modules.
          </p>
          <div className="space-y-1.5">
            {data.duplicates.map((d, i) => (
              <div
                key={i}
                className="flex items-center gap-2 text-xs font-mono"
                data-testid={`admin-routes-dup-${i}`}
              >
                <span className={METHOD_CLS[d.method] || 'text-slate-300'}>
                  {d.method}
                </span>
                <span className="text-white">{d.path}</span>
                <Badge className="text-[9px] bg-rose-500/20 text-rose-300 border-rose-500/40 ml-auto">
                  ×{d.count}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Filter path or handler name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            data-testid="admin-routes-search"
            className="w-full bg-slate-950/60 border border-slate-700 rounded pl-8 pr-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500/60"
          />
        </div>
        <select
          value={moduleFilter}
          onChange={(e) => setModuleFilter(e.target.value)}
          data-testid="admin-routes-module-filter"
          className="bg-slate-950/60 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-200"
        >
          <option value="">All modules</option>
          {data?.by_module && Object.entries(data.by_module).map(([m, c]) => (
            <option key={m} value={m}>{m} ({c})</option>
          ))}
        </select>
        <select
          value={gateFilter}
          onChange={(e) => setGateFilter(e.target.value)}
          data-testid="admin-routes-gate-filter"
          className="bg-slate-950/60 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-200"
        >
          <option value="">All gates</option>
          {data?.by_gate && Object.entries(data.by_gate).map(([g, c]) => (
            <option key={g} value={g}>{g} ({c})</option>
          ))}
        </select>
        <Button size="sm" variant="outline" onClick={fetchRoutes}
          className="text-[10px] h-7 px-2 bg-slate-800 border-slate-400/30 text-white"
          data-testid="admin-routes-refresh-btn">
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {error && (
        <Card className="bg-rose-900/30 border-rose-500/40 rounded-xl p-3" data-testid="admin-routes-error">
          <p className="text-xs text-rose-300">{error}</p>
        </Card>
      )}

      {/* Route table */}
      <div className="overflow-x-auto rounded-xl border border-slate-400/30/40">
        <table className="w-full text-sm" data-testid="admin-routes-table">
          <thead>
            <tr className="border-b border-slate-400/30/40 bg-slate-700/60">
              <th className="text-left text-slate-300 text-xs font-medium px-3 py-2.5 w-16">Method</th>
              <th className="text-left text-slate-300 text-xs font-medium px-3 py-2.5">Path</th>
              <th className="text-left text-slate-300 text-xs font-medium px-3 py-2.5">Module</th>
              <th className="text-left text-slate-300 text-xs font-medium px-3 py-2.5">Handler</th>
              <th className="text-left text-slate-300 text-xs font-medium px-3 py-2.5 w-20">Gate</th>
            </tr>
          </thead>
          <tbody>
            {filteredRoutes.length === 0 && !loading && (
              <tr>
                <td colSpan={5} className="text-center text-slate-500 text-xs py-8">
                  No routes match the current filter.
                </td>
              </tr>
            )}
            {filteredRoutes.map((r, i) => {
              const gate = GATE_CONFIG[r.gate] || GATE_CONFIG.unknown;
              const GateIcon = gate.Icon;
              return (
                <tr
                  key={`${r.method}-${r.path}-${i}`}
                  className="border-b border-slate-600/30/40 hover:bg-slate-700/35"
                  data-testid={`admin-routes-row-${i}`}
                >
                  <td className="px-3 py-2 text-xs font-mono">
                    <span className={METHOD_CLS[r.method] || 'text-slate-300'}>
                      {r.method}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs font-mono text-white truncate max-w-[300px]">
                    {r.path}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-400 font-mono">{r.module}</td>
                  <td className="px-3 py-2 text-xs text-slate-300 font-mono">{r.name}</td>
                  <td className="px-3 py-2">
                    <Badge className={`text-[9px] inline-flex items-center gap-1 border ${gate.cls}`}>
                      <GateIcon className="w-2.5 h-2.5" />
                      {gate.label}
                    </Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {data && (
        <p className="text-[10px] text-slate-500 text-center" data-testid="admin-routes-count-footer">
          Showing {filteredRoutes.length} of {data.total} routes
        </p>
      )}
    </div>
  );
};

export default AdminRoutesPanel;
