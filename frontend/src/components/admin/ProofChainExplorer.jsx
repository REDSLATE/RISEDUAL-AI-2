import React, { useEffect, useState, useCallback } from 'react';
import { Search, ShieldCheck, ShieldAlert, ChevronRight, ChevronDown, RefreshCw } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { Card } from '../ui/card';
import { Input } from '../ui/input';
import { Button } from '../ui/button';
import { toast } from 'sonner';
import MemoryDriftCard from './MemoryDriftCard';

const API = `${getApiBase()}/api/admin/proof-chain`;

/**
 * ProofChainExplorer — Patent J admin panel.
 *
 * Three panes:
 *   1. Stats summary (total blocks, by event_type, by actor)
 *   2. Entity list (search by entity_id; click to inspect)
 *   3. Chain detail (full hash-linked block sequence with verify status)
 *
 * Owner-only. The `/api/admin/proof-chain/*` routes 403 non-owners.
 */
const ProofChainExplorer = () => {
  const [stats, setStats] = useState(null);
  const [entities, setEntities] = useState([]);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [chain, setChain] = useState(null);
  const [expanded, setExpanded] = useState(new Set());
  const [loading, setLoading] = useState(false);

  const loadStats = useCallback(async () => {
    try {
      const r = await authFetch(`${API}/stats`);
      if (r.ok) setStats(await r.json());
    } catch {
      // non-critical
    }
  }, []);

  const loadEntities = useCallback(async (q) => {
    setLoading(true);
    try {
      const url = new URL(`${API}/entities`);
      url.searchParams.set('limit', '50');
      if (q) url.searchParams.set('search', q);
      const r = await authFetch(url.toString());
      if (r.ok) {
        const data = await r.json();
        setEntities(data.entities || []);
      }
    } catch {
      toast.error('Failed to load entities');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadChain = useCallback(async (entityId) => {
    setLoading(true);
    setSelected(entityId);
    setChain(null);
    setExpanded(new Set());
    try {
      const r = await authFetch(`${API}/${encodeURIComponent(entityId)}`);
      if (r.ok) {
        const data = await r.json();
        setChain(data);
      } else {
        toast.error('Failed to load chain');
      }
    } catch {
      toast.error('Failed to load chain');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadStats(); loadEntities(''); }, [loadStats, loadEntities]);

  const toggleBlock = (idx) => {
    const next = new Set(expanded);
    next.has(idx) ? next.delete(idx) : next.add(idx);
    setExpanded(next);
  };

  const eventTypeColors = {
    SIGNAL_CREATED: 'text-blue-300 bg-blue-500/10 border-blue-500/30',
    ADVERSARIAL_DECISION: 'text-purple-300 bg-purple-500/10 border-purple-500/30',
    FAILURE_MODE_CLASSIFIED: 'text-orange-300 bg-orange-500/10 border-orange-500/30',
    RISK_BUDGET_APPLIED: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
  };

  return (
    <div className="space-y-4" data-testid="proof-chain-explorer">
      {/* Memory drift detector — Mongo→Chroma sync observability.
          Sits at the top of Patent J because corrupted memory is
          the single fastest way for the proof chain's downstream
          analytics (replay, post-mortem) to lie. */}
      <MemoryDriftCard />

      {/* Stats */}
      {stats && (
        <Card className="p-4 bg-slate-800/40 border-slate-700/40">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-white text-sm font-semibold uppercase tracking-wider">
              Patent J — Proof Chain
            </h3>
            <button
              onClick={() => { loadStats(); loadEntities(search); }}
              className="text-slate-400 hover:text-white"
              title="Refresh"
              data-testid="proof-chain-refresh"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            <div>
              <div className="text-xs text-slate-400 mb-1">Total blocks</div>
              <div className="text-lg font-bold text-white" data-testid="proof-chain-total">
                {stats.total.toLocaleString()}
              </div>
            </div>
            {Object.entries(stats.by_event_type || {}).map(([type, n]) => (
              <div key={type}>
                <div className="text-[10px] text-slate-400 mb-1 uppercase tracking-wider">
                  {type.replace(/_/g, ' ').toLowerCase()}
                </div>
                <div className="text-base font-semibold text-white">{n}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Entity list */}
        <Card className="p-4 bg-slate-800/40 border-slate-700/40">
          <div className="flex items-center gap-2 mb-3">
            <Search className="w-4 h-4 text-slate-400 flex-shrink-0" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') loadEntities(search); }}
              placeholder="Search entity_id (e.g. crypto:BTC, equity:user:...)..."
              className="bg-slate-900/50 border-slate-700 text-white text-xs"
              data-testid="proof-chain-search"
            />
            <Button
              size="sm"
              onClick={() => loadEntities(search)}
              data-testid="proof-chain-search-btn"
            >
              Search
            </Button>
          </div>
          <div className="space-y-1 max-h-[420px] overflow-y-auto" data-testid="proof-chain-entity-list">
            {entities.length === 0 && !loading && (
              <div className="text-slate-500 text-xs italic py-4 text-center">No entities yet.</div>
            )}
            {entities.map((e) => (
              <button
                key={e.entity_id}
                onClick={() => loadChain(e.entity_id)}
                className={`w-full text-left px-3 py-2 rounded border transition-colors text-xs ${
                  selected === e.entity_id
                    ? 'bg-teal-500/15 border-teal-500/40 text-white'
                    : 'bg-slate-900/30 border-slate-700/30 text-slate-300 hover:bg-slate-800/50'
                }`}
                data-testid={`proof-chain-entity-${e.entity_id}`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono truncate">{e.entity_id}</span>
                  <span className="text-slate-500 flex-shrink-0 ml-2">{e.n_blocks} blocks</span>
                </div>
                <div className="flex items-center justify-between mt-1 text-[10px]">
                  <span className="text-slate-500">{e.actor}</span>
                  <span className="text-slate-500">
                    {e.latest_at ? new Date(e.latest_at).toLocaleTimeString() : ''}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </Card>

        {/* Chain detail */}
        <Card className="p-4 bg-slate-800/40 border-slate-700/40">
          {!chain ? (
            <div className="text-slate-500 text-xs italic py-12 text-center">
              Select an entity to view its proof chain.
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <div className="text-white text-xs font-mono break-all" data-testid="proof-chain-detail-entity">
                    {chain.entity_id}
                  </div>
                  <div className="text-slate-400 text-[11px]">{chain.n_blocks} blocks</div>
                </div>
                <div
                  className={`flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider ${
                    chain.valid
                      ? 'text-emerald-300 bg-emerald-500/10 border border-emerald-500/30'
                      : 'text-red-300 bg-red-500/10 border border-red-500/30'
                  }`}
                  data-testid="proof-chain-verify-status"
                >
                  {chain.valid ? <ShieldCheck className="w-3 h-3" /> : <ShieldAlert className="w-3 h-3" />}
                  {chain.valid ? 'Verified' : 'Tampered'}
                </div>
              </div>

              {!chain.valid && chain.issues?.length > 0 && (
                <div className="mb-3 rounded bg-red-500/10 border border-red-500/30 p-2 text-red-200 text-[11px]">
                  Issues: {chain.issues.join(', ')}
                </div>
              )}

              <div className="space-y-1 max-h-[400px] overflow-y-auto">
                {chain.blocks.map((b, idx) => (
                  <div
                    key={b.block_hash}
                    className="rounded border border-slate-700/30 bg-slate-900/30 overflow-hidden"
                    data-testid={`proof-chain-block-${idx}`}
                  >
                    <button
                      onClick={() => toggleBlock(idx)}
                      className="w-full px-3 py-2 flex items-center justify-between hover:bg-slate-800/40 transition-colors"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        {expanded.has(idx) ? <ChevronDown className="w-3 h-3 text-slate-400" /> : <ChevronRight className="w-3 h-3 text-slate-400" />}
                        <span className={`px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase tracking-wider ${
                          eventTypeColors[b.event_type] || 'text-slate-300 bg-slate-700/30 border-slate-600/30'
                        }`}>
                          {b.event_type}
                        </span>
                        <span className="text-slate-500 text-[10px] font-mono truncate">
                          {b.block_hash.slice(0, 16)}…
                        </span>
                      </div>
                      <span className="text-slate-500 text-[10px] flex-shrink-0">
                        #{idx}
                      </span>
                    </button>
                    {expanded.has(idx) && (
                      <div className="px-3 py-2 border-t border-slate-700/30 bg-slate-950/40">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-2 text-[10px]">
                          <div>
                            <span className="text-slate-500">prev_hash:</span>
                            <div className="font-mono text-slate-300 break-all">{b.prev_hash}</div>
                          </div>
                          <div>
                            <span className="text-slate-500">payload_hash:</span>
                            <div className="font-mono text-slate-300 break-all">{b.payload_hash}</div>
                          </div>
                          <div>
                            <span className="text-slate-500">actor:</span>{' '}
                            <span className="text-slate-300">{b.actor}</span>
                          </div>
                          <div>
                            <span className="text-slate-500">created:</span>{' '}
                            <span className="text-slate-300">
                              {b.created_at ? new Date(b.created_at).toLocaleString() : ''}
                            </span>
                          </div>
                        </div>
                        <details>
                          <summary className="text-slate-400 text-[10px] cursor-pointer hover:text-white">
                            payload
                          </summary>
                          <pre className="mt-1 text-[10px] text-slate-300 bg-slate-950/60 p-2 rounded overflow-x-auto">
                            {JSON.stringify(b.payload, null, 2)}
                          </pre>
                        </details>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
};

export default ProofChainExplorer;
