import React, { useState, useEffect, useCallback } from 'react';
import { X, Database, AlertTriangle, Brain, BarChart3, Clock, RefreshCw, Shield, Zap, TrendingUp, TrendingDown, ChevronRight, Lock } from 'lucide-react';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';
import PanelShell from './PanelShell';

const API = `${getApiBase()}/api`;

const FAILURE_COLORS = {
  TECH_FAKEOUT: { bg: 'bg-amber-500/15', border: 'border-amber-500/30', text: 'text-amber-300', bar: 'bg-amber-500' },
  MACRO_SHOCK: { bg: 'bg-red-500/15', border: 'border-red-500/30', text: 'text-orange-400', bar: 'bg-red-500' },
  LIQUIDITY_GAP: { bg: 'bg-purple-500/15', border: 'border-purple-500/30', text: 'text-purple-400', bar: 'bg-purple-500' },
  REGIME_SHIFT: { bg: 'bg-cyan-500/15', border: 'border-cyan-500/30', text: 'text-cyan-400', bar: 'bg-cyan-500' },
  UNKNOWN: { bg: 'bg-slate-500/15', border: 'border-slate-400/25', text: 'text-slate-400', bar: 'bg-slate-500' },
};

const StatCard = ({ icon: Icon, label, value, sub, color = 'text-white' }) => (
  <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-4" data-testid={`stat-${label.toLowerCase().replace(/\s/g, '-')}`}>
    <div className="flex items-center gap-2 mb-2">
      <Icon className={`w-4 h-4 ${color}`} />
      <span className="text-slate-300 text-xs uppercase tracking-wider">{label}</span>
    </div>
    <p className={`text-2xl font-bold ${color}`}>{value}</p>
    {sub && <p className="text-slate-300 text-xs mt-1">{sub}</p>}
  </div>
);

const formatDate = (d) => {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};

const OverviewTab = ({ stats, accuracy }) => {
  if (!stats) return null;
  // Prefer the 7-day directional rate (BUY/SELL only, most honest). Fall
  // back to inclusive 7-day, then 24h, so admins always see the most
  // actionable number available.
  const overall = accuracy?.overall || {};
  const hitRateDir = overall.accuracy_1w_directional;
  const hitRateAll = overall.accuracy_1w;
  const hitRate24 = overall.accuracy_24h;
  const primary =
    hitRateDir != null ? { val: hitRateDir, tag: '1W · dir', n: overall.total_1w_directional } :
    hitRateAll != null ? { val: hitRateAll, tag: '1W',       n: overall.total_1w } :
    hitRate24  != null ? { val: hitRate24,  tag: '24H',      n: overall.total_24h } :
    null;
  const pending = overall.pending || 0;
  const hitSub = primary
    ? `${primary.n} predictions · ${primary.tag}${hitRateAll != null && hitRateDir != null && Math.abs(hitRateAll - hitRateDir) > 0.5 ? ` · incl. NEUTRAL ${hitRateAll.toFixed(1)}%` : ''}`
    : (pending > 0 ? `${pending} pending` : 'No verified yet');

  return (
    <div className="space-y-6" data-testid="memory-overview-tab">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard icon={Database} label="Total Episodes" value={stats.total_episodes?.toLocaleString()} sub={stats.embedding_model} color="text-white" />
        <StatCard icon={Zap} label="Active" value={stats.active_episodes?.toLocaleString()} sub="Usable patterns" color="text-lime-400" />
        <StatCard icon={AlertTriangle} label="Toxic Lessons" value={stats.toxic_lessons} sub="Negative examples" color="text-orange-400" />
        <StatCard icon={TrendingUp} label="Hit Rate" value={primary ? `${primary.val.toFixed(1)}%` : '—'} sub={hitSub} color="text-[#3DE8D9]" />
      </div>

      {accuracy?.pricing_freshness?.disclaimer && (
        <p
          className="text-[10px] leading-relaxed text-slate-500 italic -mt-3"
          data-testid="memory-pricing-freshness-disclaimer"
        >
          {accuracy.pricing_freshness.disclaimer}
        </p>
      )}

      <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-5">
        <h4 className="text-white text-sm font-semibold mb-3 flex items-center gap-2">
          <Shield className="w-4 h-4 text-[#3DE8D9]" /> Memory Health
        </h4>
        <div className="space-y-3">
          <HealthRow label="ChromaDB" status={stats.initialized} detail={stats.storage_path} />
          <HealthRow label="Collection" status={true} detail={stats.collection_name} />
          <HealthRow label="MongoDB Logs" status={stats.mongodb_log_count > 0} detail={`${stats.mongodb_log_count?.toLocaleString()} entries`} />
          <HealthRow label="Last Cleanup" status={!!stats.last_cleanup} detail={stats.last_cleanup ? formatDate(stats.last_cleanup.run_at) : 'Never'} />
        </div>
      </div>

      {stats.last_cleanup && (
        <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-5">
          <h4 className="text-white text-sm font-semibold mb-3 flex items-center gap-2">
            <Clock className="w-4 h-4 text-slate-400" /> Last Cleanup Summary
          </h4>
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <p className="text-orange-400 text-xl font-bold">{stats.last_cleanup.toxic_removed}</p>
              <p className="text-slate-300 text-xs">Toxic Re-tagged</p>
            </div>
            <div>
              <p className="text-amber-300 text-xl font-bold">{stats.last_cleanup.obsolete_removed}</p>
              <p className="text-slate-300 text-xs">Obsolete Pruned</p>
            </div>
            <div>
              <p className="text-slate-300 text-xl font-bold">{stats.last_cleanup.total_after}</p>
              <p className="text-slate-300 text-xs">Episodes After</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const HealthRow = ({ label, status, detail }) => (
  <div className="flex items-center justify-between">
    <div className="flex items-center gap-2">
      <div className={`w-2 h-2 rounded-full ${status ? 'bg-emerald-400' : 'bg-red-400'}`} />
      <span className="text-slate-300 text-sm">{label}</span>
    </div>
    <span className="text-slate-300 text-xs">{detail}</span>
  </div>
);

const CleanupTab = ({ runs, onRunCleanup, loading }) => (
  <div className="space-y-4" data-testid="memory-cleanup-tab">
    <div className="flex items-center justify-between">
      <h4 className="text-white text-sm font-semibold">Cleanup History</h4>
      <Button size="sm" variant="outline" onClick={onRunCleanup} disabled={loading}
        className="text-xs bg-transparent border-slate-400/30 text-slate-300 hover:bg-slate-600/30"
        data-testid="run-cleanup-btn">
        <RefreshCw className={`w-3 h-3 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
        {loading ? 'Running...' : 'Run Now'}
      </Button>
    </div>

    <div className="space-y-2">
      {runs.length === 0 ? (
        <p className="text-slate-300 text-sm text-center py-8">No cleanup runs yet</p>
      ) : (
        runs.map((run) => (
          <CleanupRunCard key={run.run_at} run={run} />
        ))
      )}
    </div>
  </div>
);

const CleanupRunCard = ({ run }) => {
  const [expanded, setExpanded] = useState(false);
  const hasToxic = run.toxic_removed > 0;
  const hasObsolete = run.obsolete_removed > 0;

  return (
    <div className={`bg-[#111C30] border rounded-xl overflow-hidden ${hasToxic ? 'border-red-500/30' : 'border-slate-600/30'}`}>
      <button onClick={() => setExpanded(!expanded)} className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-slate-600/30/20 transition-colors">
        <div className="flex items-center gap-3">
          <Clock className="w-4 h-4 text-slate-400" />
          <span className="text-slate-300 text-sm">{formatDate(run.run_at)}</span>
          {hasToxic && <span className="text-[10px] bg-red-500/15 text-orange-400 border border-red-500/30 rounded px-1.5 py-0.5">{run.toxic_removed} toxic</span>}
          {hasObsolete && <span className="text-[10px] bg-amber-500/15 text-amber-300 border border-amber-500/30 rounded px-1.5 py-0.5">{run.obsolete_removed} obsolete</span>}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-300 text-xs">{run.total_before} {'\u2192'} {run.total_after}</span>
          <ChevronRight className={`w-3.5 h-3.5 text-slate-400 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        </div>
      </button>

      {expanded && run.toxic_details?.length > 0 && (
        <div className="px-4 pb-3 border-t border-slate-600/30/40">
          <div className="mt-2 space-y-1.5">
            {run.toxic_details.map((d, j) => (
              <div key={d.symbol ? `${d.symbol}-${j}` : `detail-${j}`} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-3 h-3 text-orange-400" />
                  <span className="text-white font-medium">{d.symbol}</span>
                  {d.failure_code && (
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${FAILURE_COLORS[d.failure_code]?.bg || ''} ${FAILURE_COLORS[d.failure_code]?.border || ''} ${FAILURE_COLORS[d.failure_code]?.text || 'text-slate-400'}`}>
                      {d.failure_code}
                    </span>
                  )}
                </div>
                <span className="text-slate-400">{d.confidence}% conf | {d.date}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const FailureTab = ({ breakdown, modes }) => {
  const total = breakdown?.total_failures || 0;
  const entries = Object.entries(breakdown?.breakdown || {});

  return (
    <div className="space-y-4" data-testid="memory-failure-tab">
      <h4 className="text-white text-sm font-semibold">Failure Mode Breakdown</h4>

      {total === 0 ? (
        <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-8 text-center">
          <BarChart3 className="w-8 h-8 text-slate-700 mx-auto mb-3" />
          <p className="text-slate-300 text-sm">No verified failures yet</p>
          <p className="text-slate-300 text-xs mt-1">Failure analysis begins once predictions are verified at 24h</p>
        </div>
      ) : (
        <div className="space-y-3">
          {entries.map(([code, info]) => {
            const pct = total > 0 ? (info.count / total * 100) : 0;
            const colors = FAILURE_COLORS[code] || FAILURE_COLORS.UNKNOWN;
            return (
              <div key={code} className={`${colors.bg} border ${colors.border} rounded-xl p-4`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className={`text-sm font-semibold ${colors.text}`}>{code}</span>
                    <span className="text-slate-300 text-xs">{info.count} failures</span>
                  </div>
                  <span className={`text-sm font-bold ${colors.text}`}>{pct.toFixed(0)}%</span>
                </div>
                <div className="w-full bg-slate-700/60 rounded-full h-1.5 mb-2">
                  <div className={`h-1.5 rounded-full ${colors.bar}`} style={{ width: `${pct}%` }} />
                </div>
                <p className="text-slate-300 text-xs">{info.description}</p>
              </div>
            );
          })}
        </div>
      )}

      <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-4">
        <h5 className="text-slate-300 text-xs uppercase tracking-wider mb-3">Failure Mode Reference</h5>
        <div className="space-y-2">
          {Object.entries(modes || {}).map(([code, desc]) => (
            <div key={code} className="flex items-start gap-2">
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${FAILURE_COLORS[code]?.bg || ''} ${FAILURE_COLORS[code]?.text || 'text-slate-400'} whitespace-nowrap`}>{code}</span>
              <span className="text-slate-300 text-xs">{desc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

const PostMortemTab = ({ postMortems }) => (
  <div className="space-y-4" data-testid="memory-postmortem-tab">
    <h4 className="text-white text-sm font-semibold">AI Post-Mortem Results</h4>

    {postMortems.length === 0 ? (
      <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-8 text-center">
        <Brain className="w-8 h-8 text-slate-700 mx-auto mb-3" />
        <p className="text-slate-300 text-sm">No post-mortems completed yet</p>
        <p className="text-slate-300 text-xs mt-1">AI post-mortem runs automatically when predictions fail at 24h verification</p>
      </div>
    ) : (
      <div className="space-y-2">
        {postMortems.map((pm) => {
          const colors = FAILURE_COLORS[pm.failure_code] || FAILURE_COLORS.UNKNOWN;
          return (
            <div key={`${pm.ticker}-${pm.run_at}`} className={`bg-[#111C30] border ${colors.border} rounded-xl p-4`}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <Brain className={`w-4 h-4 ${colors.text}`} />
                  <span className="text-white text-sm font-semibold">{pm.ticker}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${colors.bg} ${colors.border} ${colors.text}`}>{pm.failure_code}</span>
                  {pm.heuristic_code && pm.heuristic_code !== pm.failure_code && (
                    <span className="text-slate-400 text-[10px] line-through">{pm.heuristic_code}</span>
                  )}
                </div>
                <span className="text-slate-300 text-xs">{formatDate(pm.run_at)}</span>
              </div>
              {pm.reasoning && <p className="text-slate-300 text-xs leading-relaxed">{pm.reasoning}</p>}
              {pm.key_headline && pm.key_headline !== 'None' && (
                <p className="text-slate-400 text-[10px] mt-1.5 italic border-l-2 border-slate-400/30 pl-2">"{pm.key_headline}"</p>
              )}
              <div className="flex items-center gap-2 mt-2">
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${pm.source === 'ai_post_mortem' ? 'bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/20' : 'bg-slate-700 text-slate-400'}`}>
                  {pm.source === 'ai_post_mortem' ? 'AI Classified' : 'Heuristic'}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    )}
  </div>
);

const TABS = [
  { id: 'overview', label: 'Overview', icon: Database },
  { id: 'cleanup', label: 'Cleanup', icon: RefreshCw },
  { id: 'failures', label: 'Failures', icon: BarChart3 },
  { id: 'postmortem', label: 'Post-Mortem', icon: Brain },
];

const MemoryDashboard = ({ onClose, onSubscribe }) => {
  const { isPro } = useAuth();
  const [tab, setTab] = useState('overview');
  const [stats, setStats] = useState(null);
  const [accuracy, setAccuracy] = useState(null);
  const [cleanupRuns, setCleanupRuns] = useState([]);
  const [failureBreakdown, setFailureBreakdown] = useState(null);
  const [postMortems, setPostMortems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [cleanupLoading, setCleanupLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, accRes, cleanupRes, failRes, pmRes] = await Promise.all([
        authFetch(`${API}/accuracy/memory`),
        authFetch(`${API}/accuracy/stats`),
        authFetch(`${API}/accuracy/memory/cleanup/history`),
        authFetch(`${API}/accuracy/failure-breakdown`),
        authFetch(`${API}/accuracy/post-mortem/history?limit=20`),
      ]);
      setStats(await statsRes.json());
      setAccuracy(await accRes.json());
      setCleanupRuns((await cleanupRes.json()).runs || []);
      setFailureBreakdown(await failRes.json());
      setPostMortems((await pmRes.json()).post_mortems || []);
    } catch {
      // Silently handle — Pro wall will show if unauthorized
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleRunCleanup = async () => {
    setCleanupLoading(true);
    try {
      await authFetch(`${API}/accuracy/memory/cleanup?days=90&threshold=80.0`, { method: 'POST' });
      await fetchData();
    } catch (e) { logger.warn('Memory cleanup failed:', e); }
    setCleanupLoading(false);
  };

  if (!isPro) {
    return (
      <PanelShell onClose={onClose} testId="memory-dashboard" maxWidth="max-w-md">
        <Card className="bg-[#0F1A2E] border-slate-800 w-full p-8 text-center">
          <Lock className="w-10 h-10 text-slate-400 mx-auto mb-4" />
          <h3 className="text-white text-lg font-semibold mb-2">Memory Dashboard</h3>
          <p className="text-slate-300 text-sm mb-6">Visualize AI memory episodes, cleanup history, and failure analysis. Available for Pro users.</p>
          <div className="flex gap-3 justify-center">
            {onClose && <Button variant="outline" onClick={onClose} className="bg-transparent border-slate-400/30 text-slate-300">Close</Button>}
            <Button onClick={onSubscribe} className="bg-[#3DE8D9] hover:bg-[#3DE8D9]/80 text-white">Upgrade to Pro</Button>
          </div>
        </Card>
      </PanelShell>
    );
  }

  return (
    <PanelShell onClose={onClose} testId="memory-dashboard" maxWidth="max-w-4xl">
      <div className="bg-[#0F1A2E] border border-slate-800 rounded-2xl w-full overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-600/30">
          <div className="flex items-center gap-3">
            <Database className="w-5 h-5 text-[#3DE8D9]" />
            <div>
              <h2 className="text-white text-base font-semibold">Memory Dashboard</h2>
              <p className="text-slate-300 text-xs">Vector memory, cleanup history, failure analysis</p>
            </div>
          </div>
          {onClose && <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors p-1" data-testid="memory-dashboard-close">
            <X className="w-5 h-5" />
          </button>}
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-600/30">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-5 py-3 text-sm transition-colors border-b-2 ${tab === t.id
                ? 'text-[#3DE8D9] border-[#3DE8D9]'
                : 'text-slate-400 border-transparent hover:text-slate-300 hover:border-slate-400/30'
              }`}
              data-testid={`memory-tab-${t.id}`}
            >
              <t.icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="p-6 max-h-[65vh] overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <RefreshCw className="w-6 h-6 text-[#3DE8D9] animate-spin" />
            </div>
          ) : (
            <>
              {tab === 'overview' && <OverviewTab stats={stats} accuracy={accuracy} />}
              {tab === 'cleanup' && <CleanupTab runs={cleanupRuns} onRunCleanup={handleRunCleanup} loading={cleanupLoading} />}
              {tab === 'failures' && <FailureTab breakdown={failureBreakdown} modes={failureBreakdown?.failure_modes} />}
              {tab === 'postmortem' && <PostMortemTab postMortems={postMortems} />}
            </>
          )}
        </div>
      </div>
    </PanelShell>
  );
};

export default MemoryDashboard;
