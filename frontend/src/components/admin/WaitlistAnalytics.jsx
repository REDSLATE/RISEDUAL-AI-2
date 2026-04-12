import React, { useState, useEffect, useCallback } from 'react';
import { BarChart3, Users, TrendingUp, Share2, RefreshCw, Calendar, ArrowRight, Trophy } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api/waitlist`;

const StatCard = ({ label, value, sub, icon: Icon, color }) => (
  <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-3">
    <div className="flex items-center gap-2 mb-1">
      <Icon className={`w-4 h-4 ${color}`} />
      <span className="text-slate-400 text-[10px] uppercase tracking-wider">{label}</span>
    </div>
    <p className={`text-2xl font-bold ${color}`}>{value}</p>
    {sub && <p className="text-slate-500 text-[10px] mt-0.5">{sub}</p>}
  </div>
);

const MiniBar = ({ value, max, color }) => {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
};

const SignupChart = ({ data }) => {
  if (!data || data.length === 0) return <p className="text-slate-500 text-xs">No data yet</p>;
  const maxVal = Math.max(...data.map(d => d.total), 1);
  return (
    <div className="flex items-end gap-[3px] h-28">
      {data.map(d => {
        const totalH = (d.total / maxVal) * 100;
        const referredH = (d.referred / maxVal) * 100;
        return (
          <div key={d.date} className="flex-1 flex flex-col items-center gap-0.5 group relative min-w-0">
            <div className="w-full flex flex-col justify-end" style={{ height: '100%' }}>
              <div className="w-full bg-[#3DE8D9]/30 rounded-t-sm relative" style={{ height: `${totalH}%`, minHeight: d.total > 0 ? '2px' : 0 }}>
                <div className="absolute bottom-0 w-full bg-violet-500/60 rounded-t-sm" style={{ height: `${referredH > 0 ? (referredH / totalH * 100) : 0}%`, minHeight: d.referred > 0 ? '2px' : 0 }} />
              </div>
            </div>
            <span className="text-[7px] text-slate-600 truncate w-full text-center">{d.date.slice(5)}</span>
            <div className="absolute -top-8 left-1/2 -translate-x-1/2 bg-slate-900 border border-slate-700 px-2 py-1 rounded text-[9px] text-white opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-10 transition-opacity">
              {d.date}: {d.total} ({d.referred} ref)
            </div>
          </div>
        );
      })}
    </div>
  );
};

const FunnelBar = ({ funnel }) => {
  const stages = [
    { key: 'total', label: 'Total', color: 'bg-slate-500', value: funnel.total },
    { key: 'waiting', label: 'Waiting', color: 'bg-amber-400', value: funnel.waiting },
    { key: 'invited', label: 'Invited', color: 'bg-lime-400', value: funnel.invited },
    { key: 'active', label: 'Active', color: 'bg-[#3DE8D9]', value: funnel.active },
    { key: 'founding', label: 'Founding', color: 'bg-violet-400', value: funnel.founding },
  ];
  const max = Math.max(funnel.total, 1);
  return (
    <div className="space-y-2">
      {stages.map(s => (
        <div key={s.key} className="flex items-center gap-2">
          <span className="text-slate-400 text-[10px] w-16 text-right">{s.label}</span>
          <div className="flex-1 h-5 bg-slate-800 rounded-full overflow-hidden">
            <div className={`h-full rounded-full ${s.color} flex items-center px-2`} style={{ width: `${Math.max((s.value / max) * 100, s.value > 0 ? 4 : 0)}%` }}>
              <span className="text-[9px] text-white font-bold">{s.value}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};

const WaitlistAnalytics = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);

  const fetchAnalytics = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/admin/analytics?days=${days}`);
      if (res.ok) setData(await res.json());
      else toast.error('Failed to load analytics');
    } catch { toast.error('Analytics fetch error'); }
    finally { setLoading(false); }
  }, [days]);

  useEffect(() => { fetchAnalytics(); }, [fetchAnalytics]);

  if (loading && !data) {
    return (
      <div className="p-6 flex items-center justify-center">
        <RefreshCw className="w-5 h-5 animate-spin text-[#3DE8D9] mr-2" />
        <span className="text-slate-300 text-sm">Loading analytics...</span>
      </div>
    );
  }
  if (!data) return null;

  const rm = data.referral_metrics;

  return (
    <div className="p-4 space-y-5" data-testid="waitlist-analytics">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-[#3DE8D9]" />
          <h3 className="text-white font-semibold">Waitlist Analytics</h3>
        </div>
        <div className="flex items-center gap-2">
          {[7, 14, 30, 90].map(d => (
            <button key={d} onClick={() => setDays(d)}
              className={`px-2.5 py-1 rounded-lg text-[10px] font-medium transition-colors ${
                days === d ? 'bg-[#3DE8D9] text-white' : 'bg-slate-800 text-slate-400 hover:text-white'
              }`} data-testid={`analytics-period-${d}`}>
              {d}d
            </button>
          ))}
          <Button size="sm" variant="outline" onClick={fetchAnalytics}
            className="bg-slate-800 border-slate-400/30 text-slate-300 text-xs rounded-xl h-7">
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Total Signups" value={data.funnel.total} icon={Users} color="text-white" />
        <StatCard label="Referral Rate" value={`${rm.referral_rate}%`} sub={`${rm.total_referred} of ${data.funnel.total}`} icon={Share2} color="text-violet-300" />
        <StatCard label="Active Referrers" value={rm.total_referrers} sub={`driving ${rm.total_referred} signups`} icon={TrendingUp} color="text-lime-400" />
        <StatCard label="Invite→Active" value={`${rm.invite_conversion}%`} sub={`${data.funnel.active} converted`} icon={ArrowRight} color="text-[#3DE8D9]" />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Daily Signups Chart */}
        <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Calendar className="w-4 h-4 text-[#3DE8D9]" />
              <span className="text-white text-xs font-semibold">Daily Signups</span>
            </div>
            <div className="flex items-center gap-3 text-[9px]">
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-[#3DE8D9]/40 inline-block" /> Organic</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-violet-500/60 inline-block" /> Referred</span>
            </div>
          </div>
          <SignupChart data={data.daily_signups} />
        </div>

        {/* Conversion Funnel */}
        <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="w-4 h-4 text-lime-400" />
            <span className="text-white text-xs font-semibold">Conversion Funnel</span>
          </div>
          <FunnelBar funnel={data.funnel} />
        </div>
      </div>

      {/* Bottom Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Top Referrers */}
        <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Trophy className="w-4 h-4 text-amber-400" />
            <span className="text-white text-xs font-semibold">Top Referrers</span>
          </div>
          {data.top_referrers.length === 0 ? (
            <p className="text-slate-500 text-xs">No referrals yet</p>
          ) : (
            <div className="space-y-2">
              {data.top_referrers.map((r, idx) => (
                <div key={r.referral_code} className="flex items-center gap-2">
                  <span className={`text-[10px] font-bold w-5 text-right ${idx < 3 ? 'text-amber-400' : 'text-slate-500'}`}>{idx + 1}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-white text-xs truncate">{r.name || r.email}</span>
                      <Badge className={`text-[8px] px-1 ${r.status === 'founding' ? 'bg-amber-500/15 text-amber-300' : r.status === 'active' ? 'bg-[#3DE8D9]/15 text-[#3DE8D9]' : 'bg-slate-700 text-slate-400'}`}>
                        {r.status}
                      </Badge>
                    </div>
                    <MiniBar value={r.referral_count} max={data.top_referrers[0]?.referral_count || 1} color="bg-violet-500" />
                  </div>
                  <span className="text-violet-300 text-xs font-bold">{r.referral_count}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Priority Score Distribution */}
        <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <BarChart3 className="w-4 h-4 text-violet-400" />
            <span className="text-white text-xs font-semibold">Priority Score Distribution</span>
            <span className="text-slate-500 text-[9px]">(waiting users)</span>
          </div>
          {data.score_distribution.length === 0 ? (
            <p className="text-slate-500 text-xs">No data</p>
          ) : (
            <div className="space-y-2">
              {data.score_distribution.map(b => {
                const maxCount = Math.max(...data.score_distribution.map(x => x.count), 1);
                const pct = (b.count / maxCount) * 100;
                return (
                  <div key={b.range} className="flex items-center gap-2">
                    <span className="text-slate-400 text-[10px] w-12 text-right font-mono">{b.range}</span>
                    <div className="flex-1 h-4 bg-slate-800 rounded-full overflow-hidden">
                      <div className="h-full bg-gradient-to-r from-violet-600 to-[#3DE8D9] rounded-full flex items-center px-1.5" style={{ width: `${Math.max(pct, b.count > 0 ? 6 : 0)}%` }}>
                        {b.count > 0 && <span className="text-[8px] text-white font-bold">{b.count}</span>}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default WaitlistAnalytics;
