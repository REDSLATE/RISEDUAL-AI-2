import React, { useState } from 'react';
import { CheckCircle, AlertCircle, AlertTriangle, Loader2, Activity } from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api`;

const StatusIcon = ({ status }) => {
  if (status === 'PASS') return <CheckCircle className="w-4 h-4 text-lime-400" />;
  if (status === 'FAIL') return <AlertCircle className="w-4 h-4 text-red-400" />;
  return <AlertTriangle className="w-4 h-4 text-amber-400" />;
};

const SelfTestPanel = () => {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(`${API}/admin/self-test`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setReport(await res.json());
    } catch (e) {
      logger.error('Self-test run error:', e);
      setError(e?.message || 'Self-test request failed');
    } finally {
      setLoading(false);
    }
  };

  const overallColor = report?.overall === 'PASS'
    ? 'text-lime-400 border-emerald-700/50 bg-emerald-900/20'
    : 'text-red-400 border-red-700/50 bg-red-900/20';

  return (
    <Card
      className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-5"
      data-testid="self-test-card"
    >
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-xl bg-slate-700/50 border border-slate-600/50 flex items-center justify-center shrink-0">
          <Activity className="w-6 h-6 text-[#3DE8D9]" />
        </div>
        <div className="flex-1">
          <div className="flex items-center justify-between mb-2">
            <div>
              <h4 className="text-white text-sm font-semibold">System Self-Test</h4>
              <p className="text-slate-400 text-xs mt-0.5">
                Probes DB, collections, datetime safety, env, pricing consistency, and scheduler jobs.
              </p>
            </div>
            <Button
              onClick={run}
              disabled={loading}
              className="bg-[#3DE8D9] hover:bg-[#3DE8D9]/80 text-black font-semibold text-xs px-4"
              data-testid="self-test-run-btn"
            >
              {loading ? (
                <><Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> Running…</>
              ) : (
                'Run self-test'
              )}
            </Button>
          </div>

          {error && (
            <div
              className="mt-3 text-xs text-red-400 bg-red-900/20 border border-red-700/40 rounded-lg px-3 py-2"
              data-testid="self-test-error"
            >
              {error}
            </div>
          )}

          {report && (
            <div className="mt-4 space-y-3" data-testid="self-test-report">
              <div
                className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-semibold ${overallColor}`}
                data-testid="self-test-overall"
              >
                <StatusIcon status={report.overall} />
                {report.overall} — {report.passed}/{report.total} passed
                {report.failed > 0 && <span> · {report.failed} failed</span>}
                {report.warned > 0 && <span> · {report.warned} warn</span>}
              </div>

              <div className="rounded-lg border border-slate-700/40 bg-slate-900/30 divide-y divide-slate-800/70">
                {report.checks.map((c) => (
                  <div
                    key={c.name}
                    className="flex items-start gap-3 px-3 py-2 text-xs"
                    data-testid={`self-test-row-${c.name}`}
                  >
                    <StatusIcon status={c.status} />
                    <div className="flex-1 min-w-0">
                      <div className="text-white font-medium">{c.name}</div>
                      {c.info && (
                        <div className="text-slate-400 text-[11px] mt-0.5 break-words">
                          {c.info}
                        </div>
                      )}
                      {c.error && (
                        <div className="text-red-300 text-[11px] mt-0.5 break-words">
                          {c.error}
                        </div>
                      )}
                    </div>
                    <span className={`text-[10px] font-bold ${
                      c.status === 'PASS' ? 'text-lime-400'
                        : c.status === 'FAIL' ? 'text-red-400'
                        : 'text-amber-400'
                    }`}>
                      {c.status}
                    </span>
                  </div>
                ))}
              </div>

              <p className="text-slate-500 text-[10px]">
                Checked at {new Date(report.timestamp).toLocaleString()}
              </p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
};

export default SelfTestPanel;
