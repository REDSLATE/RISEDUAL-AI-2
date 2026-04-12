import React, { useState, useEffect, useCallback } from 'react';
import { Shield, AlertTriangle, Lock, Unlock, RefreshCw, Key, Users, Activity, ChevronDown, ChevronUp } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api/admin/security`;

const StatCard = ({ label, value, icon: Icon, color, subtitle }) => (
  <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-4" data-testid={`stat-${label.toLowerCase().replace(/\s/g, '-')}`}>
    <div className="flex items-center justify-between mb-2">
      <Icon className={`w-5 h-5 ${color}`} />
      <span className={`text-2xl font-bold ${color}`}>{value}</span>
    </div>
    <p className="text-slate-300 text-xs font-medium">{label}</p>
    {subtitle && <p className="text-slate-500 text-[10px] mt-0.5">{subtitle}</p>}
  </div>
);

const SecurityAudit = () => {
  const [overview, setOverview] = useState(null);
  const [failedLogins, setFailedLogins] = useState([]);
  const [rotations, setRotations] = useState([]);
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeSection, setActiveSection] = useState('overview');

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [ovRes, flRes, rotRes, connRes] = await Promise.all([
        authFetch(`${API}/overview`),
        authFetch(`${API}/failed-logins?limit=20`),
        authFetch(`${API}/oauth-rotations?limit=20`),
        authFetch(`${API}/broker-connections`),
      ]);

      if (ovRes.ok) setOverview(await ovRes.json());
      if (flRes.ok) {
        const d = await flRes.json();
        setFailedLogins(d.failed_logins || []);
      }
      if (rotRes.ok) {
        const d = await rotRes.json();
        setRotations(d.rotations || []);
      }
      if (connRes.ok) {
        const d = await connRes.json();
        setConnections(d.connections || []);
      }
    } catch (e) {
      toast.error('Failed to load security data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleUnlock = async (identifier) => {
    try {
      const res = await authFetch(`${API}/unlock/${encodeURIComponent(identifier)}`, { method: 'POST' });
      if (res.ok) {
        toast.success(`Unlocked: ${identifier}`);
        fetchAll();
      } else {
        const d = await res.json();
        toast.error(d.detail || 'Unlock failed');
      }
    } catch {
      toast.error('Unlock failed');
    }
  };

  const formatTime = (iso) => {
    if (!iso) return 'N/A';
    try {
      const d = new Date(iso);
      return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  };

  const SectionToggle = ({ id, label, icon: Icon, count }) => (
    <button
      onClick={() => setActiveSection(activeSection === id ? '' : id)}
      className="w-full flex items-center justify-between px-4 py-3 bg-[#0F1A2E] hover:bg-slate-700/30 rounded-xl transition-colors border border-slate-600/20"
      data-testid={`section-toggle-${id}`}
    >
      <div className="flex items-center gap-2">
        <Icon className="w-4 h-4 text-[#3DE8D9]" />
        <span className="text-white text-sm font-medium">{label}</span>
        {count > 0 && <Badge className="bg-slate-700 text-slate-300 text-[10px]">{count}</Badge>}
      </div>
      {activeSection === id ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
    </button>
  );

  if (loading && !overview) {
    return (
      <div className="p-6 flex items-center justify-center">
        <RefreshCw className="w-5 h-5 animate-spin text-[#3DE8D9] mr-2" />
        <span className="text-slate-300 text-sm">Loading security data...</span>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4" data-testid="security-audit-dashboard">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5 text-[#3DE8D9]" />
          <h3 className="text-white font-semibold">Security Audit</h3>
        </div>
        <Button size="sm" variant="outline" onClick={fetchAll}
          className="bg-slate-800 border-slate-400/30 text-slate-300 hover:text-white"
          data-testid="refresh-security">
          <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {/* Overview Stats */}
      {overview && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="security-stats-grid">
          <StatCard label="Failed Logins (24h)" value={overview.failed_logins_24h} icon={AlertTriangle}
            color="text-orange-400" subtitle={`${overview.failed_logins_7d} in 7 days`} />
          <StatCard label="Locked Accounts" value={overview.locked_accounts} icon={Lock}
            color={overview.locked_accounts > 0 ? "text-red-400" : "text-lime-400"} subtitle="Brute-force lockouts" />
          <StatCard label="Token Rotations (24h)" value={overview.oauth_rotations_24h} icon={Key}
            color="text-[#3DE8D9]" subtitle={`${overview.oauth_rotations_7d} in 7 days`} />
          <StatCard label="Active Broker Links" value={overview.active_broker_connections} icon={Activity}
            color="text-violet-400" subtitle={`${overview.total_users} users / ${overview.pro_users} pro`} />
        </div>
      )}

      {/* Failed Logins Section */}
      <SectionToggle id="logins" label="Failed Login Attempts" icon={AlertTriangle} count={failedLogins.length} />
      {activeSection === 'logins' && (
        <div className="space-y-1.5 pl-2" data-testid="failed-logins-list">
          {failedLogins.length === 0 ? (
            <p className="text-slate-500 text-xs px-4 py-3">No failed login records</p>
          ) : (
            failedLogins.map((fl) => (
              <div key={fl.identifier} className={`flex items-center justify-between px-3 py-2 rounded-lg border ${
                fl.is_locked ? 'bg-red-500/5 border-red-500/20' : 'bg-[#111C30] border-slate-600/20'
              }`}>
                <div className="flex items-center gap-3">
                  {fl.is_locked ? <Lock className="w-3.5 h-3.5 text-red-400" /> : <AlertTriangle className="w-3.5 h-3.5 text-orange-400" />}
                  <div>
                    <span className="text-white text-xs font-mono">{fl.identifier}</span>
                    <p className="text-slate-500 text-[10px]">
                      {fl.attempts} attempt{fl.attempts !== 1 ? 's' : ''} · Last: {formatTime(fl.last_attempt)}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {fl.is_locked && (
                    <>
                      <Badge className="bg-red-500/15 text-red-400 border-red-500/30 text-[10px]">LOCKED</Badge>
                      <Button size="sm" variant="outline"
                        className="text-[10px] px-2 py-1 h-6 bg-slate-800 border-slate-600 text-slate-300 hover:text-lime-400 hover:border-lime-600"
                        onClick={() => handleUnlock(fl.identifier)}
                        data-testid={`unlock-${fl.identifier}`}>
                        <Unlock className="w-3 h-3 mr-1" /> Unlock
                      </Button>
                    </>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* OAuth Token Rotations */}
      <SectionToggle id="rotations" label="OAuth Token Rotations" icon={Key} count={rotations.length} />
      {activeSection === 'rotations' && (
        <div className="space-y-1.5 pl-2" data-testid="oauth-rotations-list">
          {rotations.length === 0 ? (
            <p className="text-slate-500 text-xs px-4 py-3">No token rotation events recorded</p>
          ) : (
            rotations.map((r) => (
              <div key={`${r.user_id}-${r.timestamp}`} className="flex items-center justify-between px-3 py-2 rounded-lg bg-[#111C30] border border-slate-600/20">
                <div className="flex items-center gap-3">
                  <Key className="w-3.5 h-3.5 text-[#3DE8D9]" />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-white text-xs font-medium capitalize">{r.broker_id}</span>
                      <Badge className="bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/20 text-[10px]">{r.event}</Badge>
                    </div>
                    <p className="text-slate-500 text-[10px] font-mono">
                      {r.old_refresh_hash} &rarr; {r.new_refresh_hash} · Expires: {r.expires_in}s
                    </p>
                  </div>
                </div>
                <span className="text-slate-500 text-[10px] whitespace-nowrap">{formatTime(r.timestamp)}</span>
              </div>
            ))
          )}
        </div>
      )}

      {/* Broker Connections */}
      <SectionToggle id="connections" label="Active Broker Connections" icon={Activity} count={connections.length} />
      {activeSection === 'connections' && (
        <div className="space-y-1.5 pl-2" data-testid="broker-connections-list">
          {connections.length === 0 ? (
            <p className="text-slate-500 text-xs px-4 py-3">No active broker connections</p>
          ) : (
            connections.map((c) => (
              <div key={`${c.user_id}-${c.broker_id}`} className="flex items-center justify-between px-3 py-2 rounded-lg bg-[#111C30] border border-slate-600/20">
                <div className="flex items-center gap-3">
                  <Activity className="w-3.5 h-3.5 text-violet-400" />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-white text-xs font-medium capitalize">{c.broker_id}</span>
                      <Badge className={`text-[10px] ${c.auth_method === 'oauth' ? 'bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/20' : 'bg-slate-700 text-slate-400'}`}>
                        {c.auth_method === 'oauth' ? 'OAuth' : 'API Key'}
                      </Badge>
                      {c.oauth_pkce_used && <Badge className="bg-violet-500/10 text-violet-400 border-violet-500/20 text-[10px]">PKCE</Badge>}
                      <Badge className={`text-[10px] ${c.paper ? 'bg-amber-500/10 text-amber-300 border-amber-500/20' : 'bg-lime-500/10 text-lime-400 border-lime-500/20'}`}>
                        {c.paper ? 'Paper' : 'Live'}
                      </Badge>
                    </div>
                    <p className="text-slate-500 text-[10px]">
                      Account: {c.account_id} · Connected: {formatTime(c.connected_at)}
                    </p>
                  </div>
                </div>
                <span className="text-slate-500 text-[10px] whitespace-nowrap">Used: {formatTime(c.last_used)}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};

export default SecurityAudit;
