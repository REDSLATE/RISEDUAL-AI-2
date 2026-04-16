import React, { useState, useEffect, useCallback } from 'react';
import { Brain, Shield, BarChart3, Activity, Power, Lock, Unlock, RefreshCw, Zap, TrendingUp, AlertTriangle, Bot } from 'lucide-react';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Card } from './ui/card';
import { toast } from './ui/sonner';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = getApiBase();

const TIER_CONFIG = [
  { key: 'tier1_alerts', label: 'Smart Alerts', icon: Zap, desc: 'Push notifications when AI detects high-confidence setups', color: 'amber', req: '55% accuracy, 100+ predictions' },
  { key: 'tier2_paper', label: 'Paper Trading', icon: TrendingUp, desc: 'Auto-place simulated trades with Kelly position sizing', color: 'cyan', req: '60% accuracy, Sharpe 1.0+, 500+ predictions' },
  { key: 'tier3_live', label: 'Live Execution', icon: Shield, desc: 'Real trades via Alpaca with 2% position cap', color: 'emerald', req: '62% accuracy, Sharpe 1.2+, 1000+ predictions, 30 days' },
];

const ProgressBar = ({ value, max, label, color = 'cyan' }) => {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const colors = { cyan: 'bg-[#3DE8D9]', amber: 'bg-amber-500', emerald: 'bg-emerald-500', violet: 'bg-violet-500' };
  return (
    <div>
      <div className="flex justify-between text-[10px] mb-1">
        <span className="text-slate-400">{label}</span>
        <span className="text-slate-300 font-mono">{value} / {max}</span>
      </div>
      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full ${colors[color] || colors.cyan} rounded-full transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
};

export default function MLControls() {
  const [gate, setGate] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [gateRes, statsRes] = await Promise.all([
        authFetch(`${API}/ml/gate-status`),
        authFetch(`${API}/ml/stats`),
      ]);
      if (gateRes.ok) setGate(await gateRes.json());
      if (statsRes.ok) setStats(await statsRes.json());
    } catch (e) {
      toast.error('Failed to load ML status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Map v6 response shapes to UI variables
  const dataProgress = stats?.data_progress || {};
  const snapshots = {
    total: dataProgress.total_snapshots || 0,
    labeled: dataProgress.labeled || 0,
    unlabeled: dataProgress.pending_labels || 0,
    errors: 0,
  };
  const calibration = stats?.calibration || {};
  const model = {
    trained: Object.keys(calibration).length > 0,
    stats: Object.keys(calibration).length > 0 ? calibration : null,
  };
  const activity = {
    alerts: 0,
    paper_trades: stats?.paper_trading?.total_trades || 0,
    live_trades: stats?.live_execution?.total_orders || 0,
  };
  const milestones = stats?.milestones || {};
  const patterns = stats?.pattern_detection_counts || {};

  // Gate tiers from nested v6 response
  const gateTiers = gate?.tiers || {};
  const gateBlockers = Object.values(gateTiers)
    .filter(t => !t?.unlocked && t?.reason)
    .map(t => t.reason);

  const totalPatternDetections = Object.values(patterns).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-6" data-testid="ml-controls">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-violet-600 to-purple-500 rounded-xl flex items-center justify-center">
            <Brain className="w-5 h-5 text-white" />
          </div>
          <div>
            <h2 className="text-white text-lg font-bold">ML Controls</h2>
            <p className="text-slate-400 text-xs">Signal model status, tier gates, and autonomous actions</p>
          </div>
        </div>
        <Button size="sm" variant="outline" onClick={fetchData} disabled={loading}
          className="bg-slate-800 border-slate-600 text-slate-300 text-xs" data-testid="ml-refresh">
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {/* Tier Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {TIER_CONFIG.map(tier => {
          const unlocked = gateTiers?.[tier.key]?.unlocked || false;
          const Icon = tier.icon;
          return (
            <Card key={tier.key} className={`p-4 border ${unlocked ? `border-${tier.color}-500/30 bg-${tier.color}-500/5` : 'border-slate-700/40 bg-slate-800/30'}`}
              data-testid={`tier-card-${tier.key}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Icon className={`w-4 h-4 ${unlocked ? `text-${tier.color}-400` : 'text-slate-500'}`} />
                  <span className="text-white text-sm font-semibold">{tier.label}</span>
                </div>
                {unlocked ? (
                  <Badge className="bg-emerald-500/15 text-emerald-400 border-0 text-[9px]">
                    <Unlock className="w-2.5 h-2.5 mr-0.5" /> READY
                  </Badge>
                ) : (
                  <Badge className="bg-slate-700 text-slate-400 border-0 text-[9px]">
                    <Lock className="w-2.5 h-2.5 mr-0.5" /> LOCKED
                  </Badge>
                )}
              </div>
              <p className="text-slate-400 text-[10px] mb-2">{tier.desc}</p>
              <p className="text-slate-500 text-[9px] font-mono">{tier.req}</p>
            </Card>
          );
        })}
      </div>

      {/* Blockers */}
      {gateBlockers.length > 0 && (
        <div className="bg-amber-500/10 border border-amber-500/20 rounded-xl p-3 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-amber-300 text-xs font-semibold mb-1">Gate blockers:</p>
            {gateBlockers.map((b, i) => (
              <p key={i} className="text-amber-400/80 text-[10px]">{b}</p>
            ))}
          </div>
        </div>
      )}

      {/* Next Milestone */}
      {gate?.next_milestone && (
        <div className="bg-violet-500/10 border border-violet-500/20 rounded-xl p-3 flex items-start gap-2">
          <Activity className="w-4 h-4 text-violet-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-violet-300 text-xs font-semibold">{gate.next_milestone.milestone}</p>
            <p className="text-violet-400/70 text-[10px]">{gate.next_milestone.description}</p>
          </div>
        </div>
      )}

      {/* Data Collection Progress */}
      <Card className="p-4 border border-slate-700/40 bg-slate-800/20">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 className="w-4 h-4 text-[#3DE8D9]" />
          <span className="text-white text-sm font-semibold">Data Collection</span>
          <Badge className="bg-slate-700 text-slate-300 border-0 text-[9px] ml-auto">
            {snapshots.total || 0} snapshots
          </Badge>
        </div>
        <div className="space-y-3">
          <ProgressBar value={snapshots.labeled || 0} max={100} label="First model training" color="cyan" />
          <ProgressBar value={snapshots.labeled || 0} max={500} label="Tier 1 eligibility" color="amber" />
          <ProgressBar value={snapshots.labeled || 0} max={1000} label="Tier 3 eligibility" color="emerald" />
        </div>
        <div className="grid grid-cols-4 gap-2 mt-4">
          <div className="text-center">
            <p className="text-white text-base font-bold">{snapshots.total || 0}</p>
            <p className="text-slate-500 text-[9px]">Total</p>
          </div>
          <div className="text-center">
            <p className="text-[#3DE8D9] text-base font-bold">{snapshots.labeled || 0}</p>
            <p className="text-slate-500 text-[9px]">Labeled</p>
          </div>
          <div className="text-center">
            <p className="text-amber-400 text-base font-bold">{snapshots.unlabeled || 0}</p>
            <p className="text-slate-500 text-[9px]">Pending</p>
          </div>
          <div className="text-center">
            <p className="text-red-400 text-base font-bold">{snapshots.errors || 0}</p>
            <p className="text-slate-500 text-[9px]">Errors</p>
          </div>
        </div>
      </Card>

      {/* Model Status + Patterns side by side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* Model Status */}
        <Card className="p-4 border border-slate-700/40 bg-slate-800/20">
          <div className="flex items-center gap-2 mb-3">
            <Activity className="w-4 h-4 text-violet-400" />
            <span className="text-white text-sm font-semibold">Signal Model</span>
            <Badge className={`ml-auto border-0 text-[9px] ${model.trained ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-700 text-slate-400'}`}>
              {model.trained ? 'TRAINED' : 'COLLECTING DATA'}
            </Badge>
          </div>
          {model.stats ? (
            <div className="space-y-2 text-[11px]">
              <div className="flex justify-between"><span className="text-slate-400">Accuracy</span><span className="text-white font-mono">{(model.stats.accuracy * 100).toFixed(1)}%</span></div>
              <div className="flex justify-between"><span className="text-slate-400">Brier Score</span><span className="text-white font-mono">{model.stats.brier_score.toFixed(4)}</span></div>
              <div className="flex justify-between"><span className="text-slate-400">ECE</span><span className="text-white font-mono">{model.stats.ece.toFixed(4)}</span></div>
              <div className="flex justify-between"><span className="text-slate-400">Predictions</span><span className="text-white font-mono">{model.stats.n_predictions}</span></div>
            </div>
          ) : (
            <p className="text-slate-500 text-[10px]">Train the model after collecting 100+ labeled predictions. Run: <code className="text-violet-400">python scripts/train_signal_model.py</code></p>
          )}
        </Card>

        {/* Pattern Detection */}
        <Card className="p-4 border border-slate-700/40 bg-slate-800/20">
          <div className="flex items-center gap-2 mb-3">
            <Zap className="w-4 h-4 text-amber-400" />
            <span className="text-white text-sm font-semibold">Pattern Detection</span>
            <Badge className="ml-auto bg-slate-700 text-slate-300 border-0 text-[9px]">{totalPatternDetections} detected</Badge>
          </div>
          <div className="space-y-1.5">
            {Object.entries(patterns).map(([name, count]) => (
              <div key={name} className="flex items-center justify-between text-[10px]">
                <span className="text-slate-400">{name.replace('pattern_', '').replace(/_/g, ' ')}</span>
                <span className={`font-mono ${count > 0 ? 'text-amber-400' : 'text-slate-600'}`}>{count}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Autonomous Activity */}
      <Card className="p-4 border border-slate-700/40 bg-slate-800/20">
        <div className="flex items-center gap-2 mb-3">
          <Bot className="w-4 h-4 text-[#3DE8D9]" />
          <span className="text-white text-sm font-semibold">Autonomous Activity</span>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-slate-900/50 rounded-lg p-3 text-center">
            <p className="text-amber-400 text-xl font-bold">{activity.alerts || 0}</p>
            <p className="text-slate-500 text-[9px]">Alerts Sent</p>
          </div>
          <div className="bg-slate-900/50 rounded-lg p-3 text-center">
            <p className="text-[#3DE8D9] text-xl font-bold">{activity.paper_trades || 0}</p>
            <p className="text-slate-500 text-[9px]">Paper Trades</p>
          </div>
          <div className="bg-slate-900/50 rounded-lg p-3 text-center">
            <p className="text-emerald-400 text-xl font-bold">{activity.live_trades || 0}</p>
            <p className="text-slate-500 text-[9px]">Live Trades</p>
          </div>
        </div>
      </Card>

      {/* Milestones */}
      <Card className="p-4 border border-slate-700/40 bg-slate-800/20">
        <span className="text-white text-sm font-semibold mb-3 block">Milestones</span>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          {Object.entries(milestones).map(([key, reached]) => (
            <div key={key} className={`text-center py-2 px-1 rounded-lg ${reached ? 'bg-emerald-500/10' : 'bg-slate-900/30'}`}>
              {reached ? <Unlock className="w-3 h-3 text-emerald-400 mx-auto mb-1" /> : <Lock className="w-3 h-3 text-slate-600 mx-auto mb-1" />}
              <p className={`text-[9px] ${reached ? 'text-emerald-400' : 'text-slate-500'}`}>{key.replace(/_/g, ' ')}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
