import React, { useState, useEffect, useCallback } from 'react';
import { DollarSign, Check, X, Filter, RefreshCw } from 'lucide-react';
import { Button } from '../ui/button';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api`;

const SuccessFeesTab = () => {
  const [stats, setStats] = useState(null);
  const [records, setRecords] = useState([]);
  const [statusFilter, setStatusFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, listRes] = await Promise.all([
        authFetch(`${API}/success-fee/admin/stats`),
        authFetch(`${API}/success-fee/admin/list?status=${statusFilter}`),
      ]);
      if (statsRes.ok) setStats(await statsRes.json());
      if (listRes.ok) {
        const data = await listRes.json();
        setRecords(data.records || []);
      }
    } catch (e) {
      logger.error('Fee admin fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const doAction = async (userId, period, action) => {
    const key = `${userId}-${period}-${action}`;
    setActionLoading(key);
    try {
      const res = await authFetch(`${API}/success-fee/admin/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, period, note: `Admin ${action} via panel` }),
      });
      if (res.ok) {
        toast.success(`Fee ${action === 'mark-paid' ? 'marked as paid' : 'waived'}`);
        fetchData();
      } else {
        const d = await res.json();
        toast.error(d.detail || 'Action failed');
      }
    } catch (e) {
      toast.error('Action failed');
    } finally {
      setActionLoading(null);
    }
  };

  const FILTERS = ['all', 'unpaid', 'paid', 'waived', 'pending'];

  return (
    <div className="p-4 sm:p-6" data-testid="admin-success-fees">
      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
          <StatCard label="Total Outstanding" value={`$${(stats.total_outstanding || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`} color="text-amber-400" />
          <StatCard label="Total Collected" value={`$${(stats.total_collected || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`} color="text-lime-400" />
          <StatCard label="Unpaid Records" value={stats.unpaid || 0} color="text-red-400" />
          <StatCard label="This Month" value={stats.current_month_records || 0} color="text-[#3DE8D9]" />
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <Filter className="w-3.5 h-3.5 text-slate-400" />
        {FILTERS.map(f => (
          <button
            key={f}
            onClick={() => setStatusFilter(f)}
            className={`text-[10px] font-medium px-2.5 py-1 rounded-full transition-colors ${
              statusFilter === f
                ? 'bg-[#3DE8D9]/20 text-[#3DE8D9]'
                : 'bg-slate-800/60 text-slate-400 hover:text-slate-300'
            }`}
            data-testid={`fee-filter-${f}`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
        <Button variant="outline" size="sm" onClick={fetchData} className="ml-auto bg-slate-800 border-slate-600 text-white h-7">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* Info bar */}
      <div className="bg-slate-800/50 rounded-lg px-3 py-2 mb-4 flex items-center gap-2">
        <DollarSign className="w-3.5 h-3.5 text-[#3DE8D9]" />
        <p className="text-slate-400 text-[10px]">
          Success fee: <span className="text-white font-semibold">1.5%</span> on broker-connected gains above <span className="text-white font-semibold">$1,000</span>/month. No fee on losses or below threshold.
        </p>
      </div>

      {/* Records table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs" data-testid="fee-records-table">
          <thead>
            <tr className="text-slate-400 text-[10px] border-b border-slate-600/30">
              <th className="text-left py-2 px-2">User</th>
              <th className="text-left py-2 px-2">Period</th>
              <th className="text-right py-2 px-2">Profit</th>
              <th className="text-right py-2 px-2">Fee</th>
              <th className="text-center py-2 px-2">Status</th>
              <th className="text-right py-2 px-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {records.length === 0 ? (
              <tr><td colSpan={6} className="text-center text-slate-500 py-8">No fee records found</td></tr>
            ) : (
              records.map(r => {
                const key = `${r.user_id}-${r.period}`;
                return (
                  <tr key={key} className="border-b border-slate-700/30 hover:bg-slate-800/30">
                    <td className="py-2 px-2">
                      <p className="text-white text-[10px] font-medium truncate max-w-[140px]">{r.user_email}</p>
                    </td>
                    <td className="py-2 px-2 text-slate-300">{r.period}</td>
                    <td className={`py-2 px-2 text-right font-medium ${(r.profit || 0) > 0 ? 'text-lime-400' : 'text-red-400'}`}>
                      {(r.profit || 0) > 0 ? '+' : ''}{(r.profit || 0) < 0 ? '-' : ''}${Math.abs(r.profit || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className="py-2 px-2 text-right text-amber-400 font-semibold">
                      {(r.fee_amount || 0) > 0 ? `$${r.fee_amount.toFixed(2)}` : '--'}
                    </td>
                    <td className="py-2 px-2 text-center">
                      <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-full ${
                        r.status === 'paid' ? 'text-lime-400 bg-lime-500/15' :
                        r.status === 'waived' ? 'text-violet-400 bg-violet-500/15' :
                        r.status === 'unpaid' ? 'text-amber-400 bg-amber-500/15' :
                        'text-slate-400 bg-slate-700/60'
                      }`}>
                        {r.status}
                      </span>
                    </td>
                    <td className="py-2 px-2 text-right">
                      {r.status === 'unpaid' && (
                        <div className="flex items-center gap-1 justify-end">
                          <button
                            onClick={() => doAction(r.user_id, r.period, 'mark-paid')}
                            disabled={actionLoading === `${key}-mark-paid`}
                            className="text-lime-400 hover:bg-lime-500/10 rounded p-1 transition-colors"
                            title="Mark as Paid"
                            data-testid={`fee-mark-paid-${key}`}
                          >
                            <Check className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => doAction(r.user_id, r.period, 'waive')}
                            disabled={actionLoading === `${key}-waive`}
                            className="text-violet-400 hover:bg-violet-500/10 rounded p-1 transition-colors"
                            title="Waive Fee"
                            data-testid={`fee-waive-${key}`}
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      )}
                      {r.status === 'paid' && r.paid_at && (
                        <span className="text-slate-500 text-[9px]">{r.paid_at.split('T')[0]}</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const StatCard = ({ label, value, color }) => (
  <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-600/20">
    <p className="text-slate-400 text-[9px] mb-1">{label}</p>
    <p className={`text-lg font-bold ${color}`}>{value}</p>
  </div>
);

export default SuccessFeesTab;
