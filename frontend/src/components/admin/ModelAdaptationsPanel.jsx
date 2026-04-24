import React, { useState, useEffect, useCallback } from 'react';
import { Brain, RefreshCw, PowerOff, Undo2, Loader2, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api/admin/adaptations`;

/**
 * ModelAdaptationsPanel — view + revert the bounded row-weight
 * adjustments that the ML retrain engine applies based on recent
 * toxic-alert patterns.
 *
 * Reads ``enabled`` from the backend — when false, the whole
 * adaptation pipeline is in dry-run mode (detection + narration
 * only, training weights untouched). A banner surfaces the state
 * so admins know why their model isn't changing.
 */
const ModelAdaptationsPanel = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [revertingId, setRevertingId] = useState(null);
  const [killing, setKilling] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(API);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      logger.error('[adaptations] fetch', e);
      setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRevert = useCallback(async (id) => {
    setRevertingId(id);
    try {
      const res = await authFetch(`${API}/${encodeURIComponent(id)}/revert`, { method: 'POST' });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d?.detail || `HTTP ${res.status}`);
      toast.success('Adaptation reverted — next retrain will ignore it.');
      await load();
    } catch (e) {
      toast.error(`Revert failed: ${e.message}`);
    } finally {
      setRevertingId(null);
    }
  }, [load]);

  const handleDisableAll = useCallback(async () => {
    if (!window.confirm('Deactivate ALL active adaptations? The next retrain will use pristine severity+regime weighting only. This cannot be undone (audit trail preserved).')) return;
    setKilling(true);
    try {
      const res = await authFetch(`${API}/disable_all`, { method: 'POST' });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d?.detail || `HTTP ${res.status}`);
      toast.success(`Kill switch engaged — ${d.deactivated} adaptation${d.deactivated === 1 ? '' : 's'} deactivated.`);
      await load();
    } catch (e) {
      toast.error(`Disable-all failed: ${e.message}`);
    } finally {
      setKilling(false);
    }
  }, [load]);

  const items = data?.items || [];
  const enabled = data?.enabled ?? false;

  return (
    <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-5" data-testid="model-adaptations-card">
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-xl bg-purple-500/10 border border-purple-500/30 flex items-center justify-center shrink-0">
          <Brain className="w-6 h-6 text-purple-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <h4 className="text-white text-sm font-semibold">ML Adaptations</h4>
              <Badge className={
                enabled
                  ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30 text-[10px]"
                  : "bg-amber-500/15 text-amber-300 border-amber-500/30 text-[10px]"
              }>
                {enabled ? 'APPLYING' : 'DRY-RUN'}
              </Badge>
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={load}
                disabled={loading}
                className="h-7 px-2 text-slate-300 hover:text-white hover:bg-slate-700/60"
                data-testid="adaptations-refresh"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </Button>
              {items.length > 0 && (
                <Button
                  onClick={handleDisableAll}
                  disabled={killing}
                  className="h-7 px-2.5 text-[10px] bg-rose-500/15 text-rose-200 hover:bg-rose-500/25 border border-rose-500/40"
                  data-testid="adaptations-kill-switch"
                >
                  {killing ? <Loader2 className="w-3 h-3 animate-spin" /> : <><PowerOff className="w-3 h-3 mr-1" /> Disable all</>}
                </Button>
              )}
            </div>
          </div>
          {!enabled && (
            <div className="mb-3 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs flex items-start gap-2" data-testid="adaptations-dryrun-banner">
              <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <div>
                <strong className="text-amber-100">Dry-run mode.</strong>{' '}
                Detection and narration are live in the Agent Activity feed, but training weights are NOT modified. Set <code className="bg-slate-900/50 px-1 rounded text-[10px]">ML_ADAPTATION_ENABLED=true</code> in backend env to activate.
              </div>
            </div>
          )}
          <p className="text-slate-300 text-xs leading-relaxed mb-3">
            Bounded row-weight adjustments applied at retrain time based on recent toxic-alert patterns. Each adaptation is capped at ±30%, expires in 14 days, and is fully reversible.
          </p>

          {error && (
            <div className="mb-3 p-2 rounded-lg bg-rose-900/30 border border-rose-700/40 text-rose-200 text-xs">{error}</div>
          )}

          {!loading && items.length === 0 && !error && (
            <div className="p-4 rounded-lg bg-slate-900/40 border border-slate-700/40 text-slate-400 text-xs" data-testid="adaptations-empty">
              No active adaptations. Retrain will use pristine severity + regime weighting. New adaptations appear here after 3+ toxic alerts share the same failure code.
            </div>
          )}

          <div className="space-y-2" data-testid="adaptations-list">
            {items.map((a) => {
              const pct = Math.round((1 - a.adjustment_factor) * 100);
              const direction = a.adjustment_factor < 1 ? 'down' : 'up';
              return (
                <div
                  key={a.adaptation_id}
                  className="rounded-lg bg-slate-900/50 border border-slate-700/40 p-3"
                  data-testid={`adaptation-row-${a.metric}`}
                >
                  <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs font-mono font-bold text-white">{a.metric}</span>
                        <Badge className={`text-[10px] ${direction === 'down' ? 'bg-rose-500/15 text-rose-200 border-rose-500/30' : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'}`}>
                          {direction === 'down' ? `−${pct}%` : `+${pct}%`} weight
                        </Badge>
                        <Badge className="bg-slate-700/60 text-slate-200 border-slate-600/40 text-[10px]">
                          {a.evidence_count} toxic event{a.evidence_count === 1 ? '' : 's'}
                        </Badge>
                      </div>
                      <p className="text-[11px] text-slate-400 mt-1 leading-snug">{a.description}</p>
                      <p className="text-[10px] text-slate-500 mt-1 tabular-nums">
                        created {a.created_at?.slice(0, 16).replace('T', ' ')} · expires {String(a.expires_at).slice(0, 10)}
                      </p>
                    </div>
                    <Button
                      onClick={() => handleRevert(a.adaptation_id)}
                      disabled={revertingId === a.adaptation_id}
                      className="h-7 px-2.5 text-[10px] bg-slate-700/60 text-slate-200 hover:bg-slate-600 shrink-0"
                      data-testid={`adaptation-revert-${a.metric}`}
                    >
                      {revertingId === a.adaptation_id ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <><Undo2 className="w-3 h-3 mr-1" /> Revert</>
                      )}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Card>
  );
};

export default ModelAdaptationsPanel;
