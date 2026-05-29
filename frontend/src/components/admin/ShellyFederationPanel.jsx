import React, { useState, useEffect, useCallback } from 'react';
import {
  Network, RefreshCw, Search, AlertTriangle, ShieldCheck, Activity,
} from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { Input } from '../ui/input';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * 5-Shelly Federation Panel.
 *
 * Read-only window into the federation memory + reasoning layer.
 * Surfaces per-node memory counts, MC shared totals, and a
 * consensus dry-run form so the operator can ask "what does the
 * federation currently know about (symbol, direction)?".
 *
 * No writes. No execution authority. Pure observation.
 */

const NODE_COLORS = {
  Alpha:    'border-cyan-500/40 bg-cyan-950/30',
  Camaro:   'border-amber-500/40 bg-amber-950/30',
  Chevelle: 'border-emerald-500/40 bg-emerald-950/30',
  RedEye:   'border-rose-500/40 bg-rose-950/30',
  MC:       'border-violet-500/40 bg-violet-950/30',
};

const RECOMMENDATION_STYLE = {
  warn:    { label: 'WARN',    cls: 'bg-rose-500/20 text-rose-300 border-rose-500/40' },
  support: { label: 'SUPPORT', cls: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' },
  neutral: { label: 'NEUTRAL', cls: 'bg-slate-500/20 text-slate-300 border-slate-500/40' },
};

const ShellyFederationPanel = () => {
  const [state, setState] = useState(null);
  const [stateLoading, setStateLoading] = useState(true);
  const [stateError, setStateError] = useState(null);

  const [consensus, setConsensus] = useState(null);
  const [consensusLoading, setConsensusLoading] = useState(false);
  const [consensusError, setConsensusError] = useState(null);

  const [symbol, setSymbol] = useState('AAPL');
  const [direction, setDirection] = useState('LONG');

  const fetchState = useCallback(async () => {
    setStateLoading(true);
    setStateError(null);
    try {
      const res = await authFetch(`${API}/admin/shelly-federation/state`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setState(await res.json());
    } catch (e) {
      logger.error('federation state fetch failed:', e);
      setStateError(e.message || String(e));
    } finally {
      setStateLoading(false);
    }
  }, []);

  useEffect(() => { fetchState(); }, [fetchState]);

  const runConsensus = useCallback(async () => {
    const s = (symbol || '').trim().toUpperCase();
    const d = (direction || '').trim().toUpperCase();
    if (!s || !d) {
      setConsensusError('Symbol and direction are required.');
      return;
    }
    setConsensusLoading(true);
    setConsensusError(null);
    try {
      const url = `${API}/admin/shelly-federation/consensus?symbol=${encodeURIComponent(s)}&direction=${encodeURIComponent(d)}`;
      const res = await authFetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setConsensus(await res.json());
    } catch (e) {
      logger.error('federation consensus fetch failed:', e);
      setConsensusError(e.message || String(e));
    } finally {
      setConsensusLoading(false);
    }
  }, [symbol, direction]);

  const rolledStyle = consensus
    ? (RECOMMENDATION_STYLE[consensus.rolled_up?.recommendation] || RECOMMENDATION_STYLE.neutral)
    : null;

  return (
    <div className="p-4 sm:p-6 space-y-6" data-testid="shelly-federation-panel">
      {/* ── Header ────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Network className="w-5 h-5 text-[#3DE8D9]" />
          <div>
            <h3 className="text-white text-sm font-semibold">
              5-Shelly Federation
            </h3>
            <p className="text-slate-400 text-xs">
              Memory + reasoning layer · authority: memory_reasoning_only
            </p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={fetchState}
          disabled={stateLoading}
          className="bg-slate-800 border-slate-400/30 text-white"
          data-testid="federation-refresh-btn"
        >
          <RefreshCw className={`w-4 h-4 ${stateLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* ── Node cards ────────────────────────────────────── */}
      {stateError && (
        <div
          className="rounded-md border border-rose-500/40 bg-rose-950/40 p-3 text-rose-300 text-xs flex items-center gap-2"
          data-testid="federation-state-error"
        >
          <AlertTriangle className="w-4 h-4" />
          {stateError}
        </div>
      )}
      {state && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Object.entries(state.nodes || {}).map(([node, info]) => (
            <Card
              key={node}
              className={`p-3 border ${NODE_COLORS[node] || 'border-slate-500/40 bg-slate-800/30'}`}
              data-testid={`federation-node-card-${node.toLowerCase()}`}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="text-white text-sm font-semibold">{node}</span>
                  {info.is_mc_node && (
                    <Badge className="bg-violet-500/20 text-violet-300 border-violet-500/40 text-[10px]">
                      VERIFIER
                    </Badge>
                  )}
                </div>
                <Activity className="w-3.5 h-3.5 text-slate-400" />
              </div>
              {info.error ? (
                <p className="text-rose-300 text-xs">{info.error}</p>
              ) : (
                <div className="space-y-1 text-xs">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Memories</span>
                    <span className="text-white font-mono" data-testid={`federation-node-${node.toLowerCase()}-memories`}>
                      {info.memories_total ?? 0}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Pending rollup</span>
                    <span className="text-white font-mono">
                      {info.memories_pending_rollup ?? 0}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Receipts</span>
                    <span className="text-white font-mono">
                      {info.reasoning_receipts_total ?? 0}
                    </span>
                  </div>
                </div>
              )}
            </Card>
          ))}
          {state.mc_shared && (
            <Card
              className="p-3 border border-indigo-500/40 bg-indigo-950/30 sm:col-span-2 lg:col-span-3"
              data-testid="federation-mc-shared-card"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-indigo-300" />
                  <span className="text-white text-sm font-semibold">
                    MC Shared (cross-brain aggregator)
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs">
                  <span className="text-slate-400">
                    Memories:{' '}
                    <span className="text-white font-mono" data-testid="federation-mc-shared-memories">
                      {state.mc_shared.memories_total ?? 0}
                    </span>
                  </span>
                  <span className="text-slate-400">
                    Receipts:{' '}
                    <span className="text-white font-mono">
                      {state.mc_shared.reasoning_receipts_total ?? 0}
                    </span>
                  </span>
                </div>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ── Consensus dry-run form ────────────────────────── */}
      <Card className="p-4 border border-slate-400/25 bg-slate-900/50">
        <div className="flex items-center gap-2 mb-3">
          <Search className="w-4 h-4 text-[#3DE8D9]" />
          <h4 className="text-white text-sm font-semibold">
            Consensus dry-run
          </h4>
          <span className="text-slate-400 text-xs">
            What does the federation currently know about (symbol, direction)?
          </span>
        </div>
        <div className="flex flex-col sm:flex-row gap-2 mb-3">
          <Input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="Symbol (e.g. AAPL)"
            className="bg-slate-800 border-slate-400/30 text-white text-sm flex-1"
            data-testid="federation-consensus-symbol"
          />
          <select
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
            className="bg-slate-800 border border-slate-400/30 text-white text-sm rounded-md px-2 py-1"
            data-testid="federation-consensus-direction"
          >
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
            <option value="HOLD">HOLD</option>
          </select>
          <Button
            onClick={runConsensus}
            disabled={consensusLoading}
            className="bg-[#3DE8D9] text-slate-900 hover:bg-[#3DE8D9]/80"
            data-testid="federation-consensus-run-btn"
          >
            {consensusLoading ? 'Querying…' : 'Run consensus'}
          </Button>
        </div>

        {consensusError && (
          <p
            className="text-rose-300 text-xs mt-2"
            data-testid="federation-consensus-error"
          >
            {consensusError}
          </p>
        )}

        {consensus && (
          <div className="space-y-3" data-testid="federation-consensus-result">
            {/* Rolled-up verdict */}
            <div className="rounded-md border border-slate-400/30 bg-slate-950/40 p-3 flex items-center justify-between">
              <div>
                <p className="text-slate-400 text-xs uppercase tracking-wider">
                  Rolled-up recommendation
                </p>
                <p className="text-slate-500 text-[10px] mt-0.5">
                  {consensus.rolled_up?.rule}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <Badge
                  className={`${rolledStyle?.cls} border text-xs px-3 py-1`}
                  data-testid="federation-consensus-verdict"
                >
                  {rolledStyle?.label || 'NEUTRAL'}
                </Badge>
                <span
                  className="text-white font-mono text-sm"
                  data-testid="federation-consensus-delta"
                >
                  Δ {(consensus.rolled_up?.confidence_delta ?? 0).toFixed(3)}
                </span>
              </div>
            </div>

            {/* Per-node reasoning */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {Object.entries(consensus.per_node || {}).map(([node, r]) => {
                const style = RECOMMENDATION_STYLE[r.recommendation] || RECOMMENDATION_STYLE.neutral;
                return (
                  <div
                    key={node}
                    className={`rounded-md border ${NODE_COLORS[node] || 'border-slate-500/40 bg-slate-800/30'} p-2 text-xs`}
                    data-testid={`federation-consensus-node-${node.toLowerCase()}`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-white font-semibold">{node}</span>
                      <Badge className={`${style.cls} border text-[10px]`}>
                        {style.label}
                      </Badge>
                    </div>
                    <p className="text-slate-400 leading-relaxed">
                      {(r.reasons || []).join(' ')}
                    </p>
                    <p className="text-slate-500 font-mono text-[10px] mt-1">
                      Δ {(r.confidence_delta ?? 0).toFixed(3)} ·{' '}
                      evidence={(r.evidence_hashes || []).length}
                    </p>
                  </div>
                );
              })}
            </div>

            {/* MC cross-brain detail */}
            {consensus.mc_cross_brain && (
              <div
                className="rounded-md border border-indigo-500/30 bg-indigo-950/20 p-3 text-xs"
                data-testid="federation-consensus-mc-detail"
              >
                <p className="text-indigo-300 font-semibold mb-1">
                  MC cross-brain reasoning
                </p>
                <p className="text-slate-400 leading-relaxed">
                  {(consensus.mc_cross_brain.reasons || []).join(' ')}
                </p>
                {consensus.mc_cross_brain.by_brain &&
                  Object.keys(consensus.mc_cross_brain.by_brain).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {Object.entries(consensus.mc_cross_brain.by_brain).map(([brain, tally]) => (
                      <span
                        key={brain}
                        className="rounded-full bg-slate-800/60 border border-slate-600/40 px-2 py-0.5 text-[10px] text-slate-300"
                      >
                        {brain}: {tally.wins}W / {tally.losses}L / {tally.total}T
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
};

export default ShellyFederationPanel;
