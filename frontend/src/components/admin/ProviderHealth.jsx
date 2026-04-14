import React, { useState, useEffect, useCallback } from 'react';
import { Activity, RefreshCw, Wifi, WifiOff, Clock, AlertTriangle, Zap, Shield, Timer } from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

const STATUS_CONFIG = {
  healthy: { color: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30', icon: Wifi, label: 'Healthy' },
  cooldown: { color: 'bg-amber-500/20 text-amber-400 border-amber-500/30', icon: Clock, label: 'Cooldown' },
  disabled: { color: 'bg-red-500/20 text-red-400 border-red-500/30', icon: WifiOff, label: 'Disabled' },
  unconfigured: { color: 'bg-slate-500/20 text-slate-400 border-slate-500/30', icon: Shield, label: 'No Key' },
};

const ERROR_BADGES = {
  rate_limit: { color: 'bg-amber-900/40 text-amber-300', label: 'Rate Limited' },
  auth: { color: 'bg-red-900/40 text-red-300', label: 'Auth Failed' },
  timeout: { color: 'bg-orange-900/40 text-orange-300', label: 'Timeout' },
  upstream: { color: 'bg-purple-900/40 text-purple-300', label: 'Upstream Down' },
  unknown: { color: 'bg-slate-700/40 text-slate-300', label: 'Error' },
};

function getProviderStatus(state) {
  if (state.disabled) return 'disabled';
  if (state.cooldown_until) {
    const cd = new Date(state.cooldown_until);
    if (cd > new Date()) return 'cooldown';
  }
  return 'healthy';
}

function formatLatency(ms) {
  if (!ms) return '--';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(iso) {
  if (!iso) return '--';
  const d = new Date(iso);
  const now = new Date();
  const diff = Math.round((now - d) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

function cooldownRemaining(iso) {
  if (!iso) return null;
  const cd = new Date(iso);
  const diff = Math.round((cd - new Date()) / 1000);
  if (diff <= 0) return null;
  if (diff < 60) return `${diff}s`;
  return `${Math.round(diff / 60)}m`;
}

const ProviderCard = ({ name, state, providerType, model }) => {
  const status = getProviderStatus(state);
  const cfg = STATUS_CONFIG[status];
  const StatusIcon = cfg.icon;
  const successRate = state.successes + state.failures > 0
    ? Math.round((state.successes / (state.successes + state.failures)) * 100)
    : 100;
  const cdLeft = cooldownRemaining(state.cooldown_until);
  const errBadge = state.last_error_type ? ERROR_BADGES[state.last_error_type] || ERROR_BADGES.unknown : null;

  return (
    <div data-testid={`provider-card-${name}`} className="bg-slate-800/60 rounded-xl border border-slate-700/50 p-4 hover:border-slate-600/50 transition-all">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <div className={`w-2.5 h-2.5 rounded-full ${status === 'healthy' ? 'bg-emerald-400 shadow-emerald-400/50 shadow-sm' : status === 'cooldown' ? 'bg-amber-400 animate-pulse' : 'bg-red-400'}`} />
          <div>
            <span className="text-white text-sm font-semibold">{name}</span>
            {model && <span className="text-slate-500 text-xs ml-2">{model}</span>}
          </div>
        </div>
        <Badge variant="outline" className={`text-[10px] px-1.5 py-0.5 ${cfg.color}`}>
          <StatusIcon className="w-3 h-3 mr-1" />
          {cfg.label}
          {cdLeft && <span className="ml-1 font-mono">{cdLeft}</span>}
        </Badge>
      </div>

      <div className="grid grid-cols-4 gap-3 mb-3">
        <div>
          <p className="text-slate-500 text-[10px] uppercase tracking-wider">Calls</p>
          <p className="text-white text-sm font-mono">{state.successes + state.failures}</p>
        </div>
        <div>
          <p className="text-slate-500 text-[10px] uppercase tracking-wider">Success</p>
          <p className={`text-sm font-mono ${successRate >= 90 ? 'text-emerald-400' : successRate >= 50 ? 'text-amber-400' : 'text-red-400'}`}>
            {successRate}%
          </p>
        </div>
        <div>
          <p className="text-slate-500 text-[10px] uppercase tracking-wider">Latency</p>
          <p className="text-slate-300 text-sm font-mono">{formatLatency(state.avg_latency_ms)}</p>
        </div>
        <div>
          <p className="text-slate-500 text-[10px] uppercase tracking-wider">Fails</p>
          <p className={`text-sm font-mono ${state.consecutive_failures > 0 ? 'text-red-400' : 'text-slate-400'}`}>
            {state.consecutive_failures}x
          </p>
        </div>
      </div>

      {(state.last_error || state.last_success_at) && (
        <div className="border-t border-slate-700/40 pt-2 space-y-1">
          {state.last_success_at && (
            <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <Zap className="w-3 h-3 text-emerald-500" />
              Last success: {formatTime(state.last_success_at)}
            </div>
          )}
          {state.last_error && (
            <div className="flex items-start gap-1.5 text-[11px]">
              <AlertTriangle className="w-3 h-3 text-red-400 mt-0.5 shrink-0" />
              <div>
                {errBadge && (
                  <span className={`inline-block text-[9px] px-1.5 py-0.5 rounded-full mr-1.5 ${errBadge.color}`}>
                    {errBadge.label}
                  </span>
                )}
                <span className="text-slate-400 break-all">{state.last_error.slice(0, 120)}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const LaneSection = ({ laneName, providers }) => {
  const entries = Object.entries(providers);
  const totalCalls = entries.reduce((s, [, st]) => s + st.successes + st.failures, 0);
  const healthyCount = entries.filter(([, st]) => getProviderStatus(st) === 'healthy').length;

  const LANE_LABELS = {
    ai: { label: 'AI Providers', icon: Zap, accent: 'text-[#3DE8D9]' },
    market_data: { label: 'Market Data', icon: Activity, accent: 'text-blue-400' },
  };
  const lane = LANE_LABELS[laneName] || { label: laneName, icon: Activity, accent: 'text-slate-300' };
  const LaneIcon = lane.icon;

  return (
    <div data-testid={`lane-${laneName}`} className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <LaneIcon className={`w-4 h-4 ${lane.accent}`} />
          <h3 className="text-white font-semibold text-sm">{lane.label}</h3>
          <Badge variant="outline" className="text-[10px] bg-slate-800 border-slate-600 text-slate-400">
            {healthyCount}/{entries.length} up
          </Badge>
        </div>
        <span className="text-slate-500 text-xs font-mono">{totalCalls} calls</span>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {entries.map(([name, state]) => (
          <ProviderCard key={name} name={name} state={state} />
        ))}
      </div>
    </div>
  );
};

const ProviderHealth = () => {
  const [lanes, setLanes] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/provider-health`);
      if (res.ok) {
        const data = await res.json();
        setLanes(data.lanes || {});
        setLastRefresh(new Date());
      }
    } catch (e) {
      logger.error('Provider health error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchHealth(); }, [fetchHealth]);

  // Auto-refresh every 30s
  useEffect(() => {
    const iv = setInterval(fetchHealth, 30000);
    return () => clearInterval(iv);
  }, [fetchHealth]);

  if (loading && !lanes) {
    return (
      <div className="p-6 text-center text-slate-400">
        <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2" />
        Loading provider health...
      </div>
    );
  }

  const laneEntries = lanes ? Object.entries(lanes) : [];

  return (
    <div data-testid="provider-health-tab" className="p-4 sm:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Timer className="w-4 h-4 text-slate-400" />
          <span className="text-slate-500 text-xs">
            {lastRefresh ? `Updated ${formatTime(lastRefresh.toISOString())}` : 'Loading...'} · Auto-refresh 30s
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={fetchHealth} disabled={loading}
          className="bg-slate-800 border-slate-600 text-slate-300 hover:text-white text-xs h-7">
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {laneEntries.length === 0 ? (
        <Card className="bg-slate-800/40 border-slate-700/50 p-8 text-center">
          <WifiOff className="w-8 h-8 text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400">No provider lanes registered</p>
        </Card>
      ) : (
        laneEntries.map(([laneName, providers]) => (
          <LaneSection key={laneName} laneName={laneName} providers={providers} />
        ))
      )}
    </div>
  );
};

export default ProviderHealth;
