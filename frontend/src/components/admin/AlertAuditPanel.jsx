import React, { useState, useEffect, useCallback } from 'react';
import { Bell, RefreshCw, CheckCircle2, AlertTriangle, MailX, ChevronDown, ChevronRight } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api`;

/**
 * Alert Audit — last 25 rows from `alerts_sent` with dedup + delivery
 * forensics: alert_id, run_id, affected tickers, persistence run,
 * email delivery outcome per recipient.
 *
 * Powered by the reserve-first toxic-spike dedup pipeline (each row
 * represents one successful reservation).
 */
const AlertAuditPanel = () => {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(`${API}/admin/alerts/audit?limit=25`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRows(data.items || []);
      setTotal(data.total || 0);
    } catch (e) {
      logger.error('[alert-audit] fetch error', e);
      setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const fmtDate = (iso) => {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  };

  return (
    <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-5" data-testid="alert-audit-card">
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center shrink-0">
          <Bell className="w-6 h-6 text-amber-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            <h4 className="text-white text-sm font-semibold">Alert Audit</h4>
            <div className="flex items-center gap-2">
              <span className="text-slate-400 text-[10px]">{total} total · showing {rows.length}</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={load}
                disabled={loading}
                className="h-7 px-2 text-slate-300 hover:text-white hover:bg-slate-700/60"
                data-testid="alert-audit-refresh-btn"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </Button>
            </div>
          </div>
          <p className="text-slate-300 text-xs leading-relaxed mb-4">
            Reserve-first dedup trail — every row is one successfully-reserved alert. Use this to confirm the unique-index gate is holding and to trace any email delivery failures (<span className="text-amber-300">email_failed</span> flag, per-recipient error).
          </p>

          {error && (
            <div className="mb-3 p-2 rounded-lg bg-rose-900/30 border border-rose-700/40 text-rose-200 text-xs">
              {error}
            </div>
          )}

          {!loading && rows.length === 0 && !error && (
            <div className="p-4 rounded-lg bg-slate-900/40 border border-slate-700/40 text-slate-400 text-xs">
              No alerts reserved yet. This is normal if no nightly cleanup has flagged toxic spikes.
            </div>
          )}

          <div className="space-y-2" data-testid="alert-audit-rows">
            {rows.map((r) => {
              const isOpen = expanded === r.alert_id;
              const hasFailure = r.email_failed;
              return (
                <div
                  key={r.alert_id}
                  className={`rounded-lg border ${hasFailure ? 'border-rose-700/40 bg-rose-950/20' : 'border-slate-700/40 bg-slate-900/40'}`}
                  data-testid={`alert-audit-row-${r.alert_id?.slice(0, 12)}`}
                >
                  <button
                    type="button"
                    onClick={() => setExpanded(isOpen ? null : r.alert_id)}
                    className="w-full p-3 flex items-center gap-3 text-left hover:bg-slate-800/40 transition-colors"
                    data-testid={`alert-audit-row-toggle-${r.alert_id?.slice(0, 12)}`}
                  >
                    {isOpen ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
                    {hasFailure ? (
                      <MailX className="w-4 h-4 text-rose-400 shrink-0" />
                    ) : (
                      <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                    )}
                    <div className="flex-1 min-w-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span className="text-white font-mono">{r.alert_id?.slice(0, 12)}…</span>
                      <Badge className="bg-slate-700/60 text-slate-200 border-slate-600/40 text-[10px] font-medium">
                        {r.alert_type}
                      </Badge>
                      <span className="text-slate-400">{fmtDate(r.created_at)}</span>
                      {typeof r.toxic_count === 'number' && (
                        <span className="text-slate-300">{r.toxic_count} toxic</span>
                      )}
                      {r.persistence_run > 1 && (
                        <Badge className="bg-amber-500/15 text-amber-300 border-amber-500/30 text-[10px]">
                          <AlertTriangle className="w-3 h-3 mr-1" /> {r.persistence_run}d streak
                        </Badge>
                      )}
                      {hasFailure && (
                        <Badge className="bg-rose-500/15 text-rose-300 border-rose-500/30 text-[10px]">
                          email_failed
                        </Badge>
                      )}
                    </div>
                  </button>

                  {isOpen && (
                    <div className="px-4 pb-3 pt-1 border-t border-slate-700/30 text-[11px] text-slate-300 space-y-1.5" data-testid={`alert-audit-details-${r.alert_id?.slice(0, 12)}`}>
                      <div><span className="text-slate-400">alert_id:</span> <span className="font-mono text-slate-200">{r.alert_id}</span></div>
                      <div><span className="text-slate-400">run_id:</span> <span className="font-mono text-slate-200">{r.run_id || '—'}</span></div>
                      <div><span className="text-slate-400">date_bucket:</span> {r.date_bucket || '—'}</div>
                      <div>
                        <span className="text-slate-400">affected tickers:</span>{' '}
                        {(r.affected_tickers?.length ?? 0) === 0 ? (
                          <span className="text-slate-500">—</span>
                        ) : (
                          r.affected_tickers.map((t) => (
                            <span key={t} className="inline-block mr-1 mb-1 px-1.5 py-0.5 rounded bg-slate-800 text-slate-200 font-mono text-[10px]">{t}</span>
                          ))
                        )}
                      </div>
                      <div>
                        <span className="text-slate-400">delivered to:</span>{' '}
                        {(r.email_recipients?.length ?? 0) === 0 ? (
                          <span className="text-slate-500">—</span>
                        ) : (
                          r.email_recipients.map((e) => (
                            <span key={e} className="inline-block mr-1 mb-1 px-1.5 py-0.5 rounded bg-emerald-900/30 border border-emerald-700/40 text-emerald-200 text-[10px]">{e}</span>
                          ))
                        )}
                      </div>
                      {hasFailure && (
                        <div>
                          <span className="text-rose-300">failed recipients:</span>
                          <div className="mt-1 space-y-1">
                            {r.email_failed_recipients?.map((f, i) => (
                              <div key={i} className="px-2 py-1 rounded bg-rose-950/40 border border-rose-800/40 text-rose-200">
                                <span className="font-mono">{f.email}</span> — <span className="text-rose-300">{f.error}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Card>
  );
};

export default AlertAuditPanel;
