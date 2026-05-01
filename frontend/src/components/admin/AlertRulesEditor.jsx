import React, { useState, useEffect, useCallback } from 'react';
import { Bell, Plus, Trash2, Play, RefreshCw, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Badge } from '../ui/badge';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';
import { toast } from 'sonner';

const API = `${getApiBase()}/api`;

const SUPPORTED_METRICS = [
  'unknown_direction_tokens',
  'grade_backfills',
  'le_trade_repairs',
  'brute_force_lockouts',
];

const COMPARATORS = [
  { value: 'gte', label: '≥ (at or above)' },
  { value: 'gt', label: '> (strictly above)' },
  { value: 'eq', label: '= (equal to)' },
];

const DEFAULT_NEW_RULE = {
  rule_id: '',
  metric: 'unknown_direction_tokens',
  window_hours: 24,
  threshold: 1,
  comparator: 'gte',
  channels_csv: 'email:admin@risedual.ai',
  enabled: true,
  throttle_hours: 12,
  notes: '',
};

/**
 * AlertRulesEditor — CRUD surface for data-integrity alert thresholds.
 *
 * Each rule points at one of the supported metrics and dispatches to
 * a comma-separated list of channels (email:<addr> or slack). The
 * scheduled evaluator runs every 15 minutes; this UI exposes a
 * "Run now" button for on-demand evaluation after adjusting a rule.
 *
 * The component intentionally stays dense — alert-rule editing is
 * infrequent and favours a compact table over a wizard.
 */
const AlertRulesEditor = () => {
  const [rules, setRules] = useState([]);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState(DEFAULT_NEW_RULE);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [rRes, eRes] = await Promise.all([
        authFetch(`${API}/admin/data-integrity/alert-rules`),
        authFetch(`${API}/admin/data-integrity/alert-events?limit=20`),
      ]);
      if (rRes.ok) setRules((await rRes.json()).rules || []);
      if (eRes.ok) setEvents((await eRes.json()).events || []);
    } catch (e) {
      logger.error('AlertRulesEditor load error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!draft.rule_id.trim()) {
      toast.error('rule_id is required');
      return;
    }
    setSaving(true);
    try {
      const channels = draft.channels_csv
        .split(',')
        .map((c) => c.trim())
        .filter(Boolean);
      const payload = {
        rule_id: draft.rule_id.trim(),
        metric: draft.metric,
        window_hours: Number(draft.window_hours) || 24,
        threshold: Number(draft.threshold) || 0,
        comparator: draft.comparator,
        channels,
        enabled: !!draft.enabled,
        throttle_hours: Number(draft.throttle_hours) || 0,
        notes: draft.notes || '',
      };
      const res = await authFetch(`${API}/admin/data-integrity/alert-rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail?.error_code || err.detail || `HTTP ${res.status}`);
      }
      setDraft(DEFAULT_NEW_RULE);
      toast.success(`Saved rule: ${payload.rule_id}`);
      load();
    } catch (e) {
      toast.error(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (ruleId) => {
    if (!window.confirm(`Delete rule "${ruleId}"?`)) return;
    try {
      await authFetch(`${API}/admin/data-integrity/alert-rules/${ruleId}`, { method: 'DELETE' });
      toast.success(`Deleted rule: ${ruleId}`);
      load();
    } catch (e) {
      toast.error(`Delete failed: ${e.message}`);
    }
  };

  const evaluate = async () => {
    setRunning(true);
    try {
      const res = await authFetch(`${API}/admin/data-integrity/alert-rules/evaluate-now`, { method: 'POST' });
      const summary = res.ok ? await res.json() : { fired: 0 };
      toast.success(
        `Evaluated ${summary.evaluated ?? 0} rules · fired ${summary.fired ?? 0} · throttled ${summary.throttled ?? 0}`,
      );
      load();
    } catch (e) {
      toast.error(`Evaluation failed: ${e.message}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="bg-slate-900/40 border border-slate-700/40 rounded-lg p-3 space-y-3" data-testid="alert-rules-editor">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
          <Bell className="w-3.5 h-3.5" />
          Alert rules
          <span className="text-[10px] text-slate-500 font-normal">
            · evaluated every 15 min
          </span>
        </div>
        <Button
          size="sm"
          onClick={evaluate}
          disabled={running}
          className="h-7 text-[11px] bg-slate-800 hover:bg-slate-700 text-white"
          data-testid="evaluate-rules-btn"
        >
          {running ? <><RefreshCw className="w-3 h-3 mr-1 animate-spin" /> Evaluating…</> : <><Play className="w-3 h-3 mr-1" /> Evaluate now</>}
        </Button>
      </div>

      {/* New-rule builder */}
      <div className="bg-slate-800/40 border border-slate-700/30 rounded-md p-2.5 space-y-2" data-testid="new-rule-form">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Input
            placeholder="rule_id (unique)"
            value={draft.rule_id}
            onChange={(e) => setDraft({ ...draft, rule_id: e.target.value })}
            className="h-7 text-[11px] bg-slate-900/60 border-slate-700 text-white"
            data-testid="new-rule-id"
          />
          <select
            value={draft.metric}
            onChange={(e) => setDraft({ ...draft, metric: e.target.value })}
            className="h-7 text-[11px] bg-slate-900/60 border border-slate-700 rounded-md text-white px-2"
            data-testid="new-rule-metric"
          >
            {SUPPORTED_METRICS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <select
            value={draft.comparator}
            onChange={(e) => setDraft({ ...draft, comparator: e.target.value })}
            className="h-7 text-[11px] bg-slate-900/60 border border-slate-700 rounded-md text-white px-2"
            data-testid="new-rule-comparator"
          >
            {COMPARATORS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
          <Input
            type="number"
            min="0"
            placeholder="threshold"
            value={draft.threshold}
            onChange={(e) => setDraft({ ...draft, threshold: e.target.value })}
            className="h-7 text-[11px] bg-slate-900/60 border-slate-700 text-white"
            data-testid="new-rule-threshold"
          />
          <Input
            type="number"
            min="1"
            placeholder="window_hours"
            value={draft.window_hours}
            onChange={(e) => setDraft({ ...draft, window_hours: e.target.value })}
            className="h-7 text-[11px] bg-slate-900/60 border-slate-700 text-white"
            data-testid="new-rule-window"
          />
          <Input
            type="number"
            min="0"
            placeholder="throttle_hours"
            value={draft.throttle_hours}
            onChange={(e) => setDraft({ ...draft, throttle_hours: e.target.value })}
            className="h-7 text-[11px] bg-slate-900/60 border-slate-700 text-white"
            data-testid="new-rule-throttle"
          />
          <Input
            placeholder="channels (e.g. slack,email:you@x.com)"
            value={draft.channels_csv}
            onChange={(e) => setDraft({ ...draft, channels_csv: e.target.value })}
            className="h-7 text-[11px] bg-slate-900/60 border-slate-700 text-white col-span-2"
            data-testid="new-rule-channels"
          />
        </div>
        <div className="flex items-center gap-2">
          <Input
            placeholder="notes (optional) — shown in the alert body"
            value={draft.notes}
            onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
            className="h-7 text-[11px] bg-slate-900/60 border-slate-700 text-white flex-1"
            data-testid="new-rule-notes"
          />
          <label className="flex items-center gap-1 text-[10px] text-slate-400 whitespace-nowrap">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
              data-testid="new-rule-enabled"
            />
            enabled
          </label>
          <Button
            size="sm"
            onClick={save}
            disabled={saving}
            className="h-7 text-[11px] bg-emerald-600 hover:bg-emerald-500 text-white"
            data-testid="save-rule-btn"
          >
            <Plus className="w-3 h-3 mr-1" /> {saving ? 'Saving…' : 'Save rule'}
          </Button>
        </div>
      </div>

      {/* Existing rules list */}
      {loading ? (
        <div className="text-[11px] text-slate-500">Loading…</div>
      ) : rules.length === 0 ? (
        <div className="text-[11px] text-slate-500 italic">
          No rules yet. Add one above to start catching drift.
        </div>
      ) : (
        <div className="space-y-1" data-testid="rules-list">
          {rules.map((r) => (
            <div
              key={r.rule_id}
              className={`flex items-center justify-between gap-2 text-[11px] px-2 py-1.5 rounded-md border ${
                r.enabled
                  ? 'bg-slate-800/40 border-slate-700/40'
                  : 'bg-slate-900/40 border-slate-800/40 opacity-60'
              }`}
            >
              <div className="flex items-center gap-2 min-w-0 flex-1">
                {r.enabled
                  ? <CheckCircle2 className="w-3 h-3 text-emerald-400 flex-shrink-0" />
                  : <AlertTriangle className="w-3 h-3 text-slate-500 flex-shrink-0" />}
                <span className="font-mono font-semibold text-slate-200 truncate">{r.rule_id}</span>
                <Badge className="bg-slate-700/40 text-slate-300 text-[9px] font-mono">
                  {r.metric} {r.comparator} {r.threshold} · {r.window_hours}h
                </Badge>
                {r.last_fired_at && (
                  <span className="text-[9px] text-amber-300 font-mono">
                    last fired {new Date(r.last_fired_at).toLocaleString()} · {r.last_fired_value}
                  </span>
                )}
              </div>
              <button
                onClick={() => remove(r.rule_id)}
                className="text-slate-500 hover:text-red-400 transition-colors flex-shrink-0"
                title="Delete rule"
                data-testid={`delete-rule-${r.rule_id}`}
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Recent events */}
      {events.length > 0 && (
        <div className="pt-2 border-t border-slate-700/40">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
            Recent events
          </div>
          <div className="space-y-0.5 font-mono" data-testid="recent-events-list">
            {events.slice(0, 5).map((e) => {
              const delivered = (e.dispatch_results || []).filter((d) => d.sent).length;
              const total = (e.dispatch_results || []).length;
              return (
                <div key={`${e.rule_id}-${e.fired_at}`} className="text-[10px] text-slate-400 flex items-center gap-2">
                  <span className="text-slate-500">{new Date(e.fired_at).toLocaleTimeString()}</span>
                  <span className="text-slate-200 truncate">{e.rule_id}</span>
                  <span className="text-amber-300">{e.count}</span>
                  <span className="text-slate-500 ml-auto">{delivered}/{total} delivered</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

export default AlertRulesEditor;
