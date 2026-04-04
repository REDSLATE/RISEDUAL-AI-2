import React, { useState, useEffect, useCallback } from 'react';
import { Users, Shield, ShieldOff, Crown, UserCheck, UserX, RefreshCw, Search, Gift, Plus, Trash2, ToggleLeft, ToggleRight, Calendar } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { authFetch } from '../contexts/AuthContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AdminPanel = ({ onClose }) => {
  const [tab, setTab] = useState('users');
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [actionLoading, setActionLoading] = useState(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/auth/admin/users`);
      if (res.ok) {
        const data = await res.json();
        setUsers(data.users);
      }
    } catch (e) {
      console.error('Error fetching users:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const doAction = async (userId, action) => {
    setActionLoading(`${userId}-${action}`);
    try {
      const res = await authFetch(`${API}/auth/admin/users/${userId}/${action}`, { method: 'POST' });
      if (res.ok) fetchUsers();
      else {
        const d = await res.json();
        alert(d.detail || 'Action failed');
      }
    } catch (e) {
      alert('Action failed');
    } finally {
      setActionLoading(null);
    }
  };

  const filtered = users.filter(u =>
    u.name?.toLowerCase().includes(filter.toLowerCase()) ||
    u.email?.toLowerCase().includes(filter.toLowerCase())
  );

  const tabs = [
    { id: 'users', label: 'Users', icon: Users },
    { id: 'promos', label: 'Promos', icon: Gift },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="admin-panel">
      <div className="bg-slate-900 rounded-2xl max-w-4xl w-full my-4 border border-slate-700/50">
        <div className="p-6 border-b border-slate-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-600 rounded-xl flex items-center justify-center">
              <Crown className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-white text-xl font-bold">Admin Panel</h2>
              <p className="text-slate-400 text-sm">{users.length} users total</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={fetchUsers} className="bg-slate-800 border-slate-700 text-white">
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>
            <button onClick={onClose} className="text-slate-400 hover:text-white text-xl px-2">x</button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-700/50 px-4">
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              data-testid={`admin-tab-${t.id}`}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-all ${
                tab === t.id ? 'text-[#0052FF] border-[#0052FF]' : 'text-slate-400 border-transparent hover:text-slate-300'
              }`}
            >
              <t.icon className="w-4 h-4" />
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'users' ? (
          <>
            <div className="p-4 border-b border-slate-700/50">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
                <Input placeholder="Search users..." value={filter} onChange={e => setFilter(e.target.value)}
                  className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl" />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="admin-users-table">
                <thead>
                  <tr className="border-b border-slate-700/40">
                    <th className="text-left text-slate-400 text-xs font-medium px-4 py-3">User</th>
                    <th className="text-left text-slate-400 text-xs font-medium px-4 py-3">Role</th>
                    <th className="text-left text-slate-400 text-xs font-medium px-4 py-3">Plan</th>
                    <th className="text-left text-slate-400 text-xs font-medium px-4 py-3">Status</th>
                    <th className="text-right text-slate-400 text-xs font-medium px-4 py-3">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map(u => (
                    <tr key={u._id} className="border-b border-slate-800/40 hover:bg-slate-800/30">
                      <td className="px-4 py-3">
                        <p className="text-white font-medium">{u.name}</p>
                        <p className="text-slate-500 text-xs">{u.email}</p>
                      </td>
                      <td className="px-4 py-3">
                        <Badge className={`text-[10px] ${
                          u.role === 'owner' ? 'bg-red-900/40 text-red-400 border-red-800' :
                          u.role === 'admin' ? 'bg-blue-900/40 text-blue-400 border-blue-800' :
                          'bg-slate-700 text-slate-400'
                        }`}>{u.role}</Badge>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-semibold ${u.subscription_status === 'pro' ? 'text-[#0052FF]' : u.subscription_status === 'trial' ? 'text-emerald-400' : 'text-slate-500'}`}>
                          {u.subscription_status === 'pro' ? 'PRO' : u.subscription_status === 'trial' ? 'TRIAL' : 'FREE'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs ${u.is_active !== false ? 'text-emerald-400' : 'text-red-400'}`}>
                          {u.is_active !== false ? 'Active' : 'Disabled'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {u.role !== 'owner' && (
                          <div className="flex items-center justify-end gap-1">
                            {u.subscription_status !== 'pro' ? (
                              <Button size="sm" variant="outline" className="text-[10px] px-2 py-1 h-7 bg-blue-900/30 text-blue-400 border-blue-800/50 hover:bg-blue-800/40"
                                disabled={actionLoading === `${u._id}-grant-pro`}
                                onClick={() => doAction(u._id, 'grant-pro')}>
                                <Shield className="w-3 h-3 mr-1" /> Grant Pro
                              </Button>
                            ) : (
                              <Button size="sm" variant="outline" className="text-[10px] px-2 py-1 h-7 bg-slate-700 text-slate-400 border-slate-600 hover:bg-slate-600"
                                disabled={actionLoading === `${u._id}-revoke-pro`}
                                onClick={() => doAction(u._id, 'revoke-pro')}>
                                <ShieldOff className="w-3 h-3 mr-1" /> Revoke Pro
                              </Button>
                            )}
                            {u.is_active !== false ? (
                              <Button size="sm" variant="outline" className="text-[10px] px-2 py-1 h-7 bg-red-900/30 text-red-400 border-red-800/50 hover:bg-red-800/40"
                                disabled={actionLoading === `${u._id}-deactivate`}
                                onClick={() => doAction(u._id, 'deactivate')}>
                                <UserX className="w-3 h-3 mr-1" /> Disable
                              </Button>
                            ) : (
                              <Button size="sm" variant="outline" className="text-[10px] px-2 py-1 h-7 bg-emerald-900/30 text-emerald-400 border-emerald-800/50 hover:bg-emerald-800/40"
                                disabled={actionLoading === `${u._id}-activate`}
                                onClick={() => doAction(u._id, 'activate')}>
                                <UserCheck className="w-3 h-3 mr-1" /> Enable
                              </Button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <PromoManager />
        )}
      </div>
    </div>
  );
};

const PromoManager = () => {
  const [promos, setPromos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    title: '',
    message: '',
    referral_target: 3,
    reward_months: 2,
    start_date: new Date().toISOString().split('T')[0],
    end_date: '',
  });
  const [creating, setCreating] = useState(false);

  const fetchPromos = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/promo/all`);
      if (res.ok) {
        const data = await res.json();
        setPromos(data.promos);
      }
    } catch (e) {
      console.error('Error fetching promos:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchPromos(); }, [fetchPromos]);

  const createPromo = async () => {
    if (!form.title || !form.end_date) return;
    setCreating(true);
    try {
      const body = {
        ...form,
        referral_target: parseInt(form.referral_target),
        reward_months: parseInt(form.reward_months),
        start_date: new Date(form.start_date).toISOString(),
        end_date: new Date(form.end_date + 'T23:59:59').toISOString(),
      };
      const res = await authFetch(`${API}/promo/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setShowCreate(false);
        setForm({ title: '', message: '', referral_target: 3, reward_months: 2, start_date: new Date().toISOString().split('T')[0], end_date: '' });
        fetchPromos();
      }
    } catch (e) {
      console.error('Create promo error:', e);
    } finally {
      setCreating(false);
    }
  };

  const togglePromo = async (id) => {
    try {
      await authFetch(`${API}/promo/${id}/toggle`, { method: 'PUT' });
      fetchPromos();
    } catch (e) {
      console.error('Toggle promo error:', e);
    }
  };

  const deletePromo = async (id) => {
    try {
      await authFetch(`${API}/promo/${id}`, { method: 'DELETE' });
      fetchPromos();
    } catch (e) {
      console.error('Delete promo error:', e);
    }
  };

  const formatDate = (d) => d ? new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';

  return (
    <div className="p-4 space-y-4" data-testid="promo-manager">
      <div className="flex items-center justify-between">
        <h3 className="text-white text-sm font-semibold">Promo Campaigns</h3>
        <Button size="sm" className="bg-[#0052FF] hover:bg-[#2563EB] text-white text-xs h-8" onClick={() => setShowCreate(!showCreate)} data-testid="create-promo-btn">
          <Plus className="w-3.5 h-3.5 mr-1" /> New Campaign
        </Button>
      </div>

      {/* Create form */}
      {showCreate && (
        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-4 space-y-3" data-testid="promo-create-form">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-slate-400 text-[10px] block mb-1">Title</label>
              <Input
                placeholder="e.g., Launch Week Special"
                value={form.title}
                onChange={e => setForm({ ...form, title: e.target.value })}
                className="bg-slate-900 border-slate-600 text-white text-sm rounded-xl h-9"
                data-testid="promo-title-input"
              />
            </div>
            <div>
              <label className="text-slate-400 text-[10px] block mb-1">Message</label>
              <Input
                placeholder="e.g., Refer 3 friends, get 2 months free!"
                value={form.message}
                onChange={e => setForm({ ...form, message: e.target.value })}
                className="bg-slate-900 border-slate-600 text-white text-sm rounded-xl h-9"
                data-testid="promo-message-input"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label className="text-slate-400 text-[10px] block mb-1">Referral Target</label>
              <Input type="number" min="1" value={form.referral_target} onChange={e => setForm({ ...form, referral_target: e.target.value })}
                className="bg-slate-900 border-slate-600 text-white text-sm rounded-xl h-9" data-testid="promo-target-input" />
            </div>
            <div>
              <label className="text-slate-400 text-[10px] block mb-1">Reward (Months)</label>
              <Input type="number" min="1" value={form.reward_months} onChange={e => setForm({ ...form, reward_months: e.target.value })}
                className="bg-slate-900 border-slate-600 text-white text-sm rounded-xl h-9" data-testid="promo-reward-input" />
            </div>
            <div>
              <label className="text-slate-400 text-[10px] block mb-1">Start Date</label>
              <Input type="date" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })}
                className="bg-slate-900 border-slate-600 text-white text-sm rounded-xl h-9" data-testid="promo-start-input" />
            </div>
            <div>
              <label className="text-slate-400 text-[10px] block mb-1">End Date</label>
              <Input type="date" value={form.end_date} onChange={e => setForm({ ...form, end_date: e.target.value })}
                className="bg-slate-900 border-slate-600 text-white text-sm rounded-xl h-9" data-testid="promo-end-input" />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" className="text-xs h-8 bg-slate-700 text-slate-300 border-slate-600" onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button size="sm" className="text-xs h-8 bg-[#0052FF] text-white" onClick={createPromo} disabled={creating || !form.title || !form.end_date} data-testid="promo-submit-btn">
              {creating ? 'Creating...' : 'Create Campaign'}
            </Button>
          </div>
        </Card>
      )}

      {/* Promo list */}
      {loading ? (
        <div className="flex justify-center py-8"><RefreshCw className="w-5 h-5 text-[#0052FF] animate-spin" /></div>
      ) : promos.length === 0 ? (
        <div className="text-center py-8">
          <Gift className="w-10 h-10 text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400 text-sm">No promo campaigns yet</p>
          <p className="text-slate-500 text-xs mt-1">Create your first campaign to drive referrals</p>
        </div>
      ) : (
        <div className="space-y-2">
          {promos.map(p => {
            const now = new Date();
            const isLive = p.is_active && new Date(p.start_date) <= now && new Date(p.end_date) >= now;
            const isExpired = new Date(p.end_date) < now;
            return (
              <div key={p.id} className="flex items-center justify-between bg-slate-800/60 border border-slate-700/40 rounded-xl px-4 py-3" data-testid={`promo-item-${p.id}`}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-white text-sm font-medium truncate">{p.title}</p>
                    {isLive && <Badge className="text-[9px] bg-emerald-900/30 text-emerald-400 border-emerald-700/50 animate-pulse">LIVE</Badge>}
                    {isExpired && <Badge className="text-[9px] bg-slate-700 text-slate-400">EXPIRED</Badge>}
                    {!p.is_active && !isExpired && <Badge className="text-[9px] bg-amber-900/30 text-amber-400 border-amber-700/50">PAUSED</Badge>}
                  </div>
                  <p className="text-slate-400 text-xs truncate">{p.message}</p>
                  <div className="flex items-center gap-3 mt-1">
                    <span className="text-slate-500 text-[10px] flex items-center gap-1"><Calendar className="w-3 h-3" /> {formatDate(p.start_date)} - {formatDate(p.end_date)}</span>
                    <span className="text-[#0052FF] text-[10px] font-medium">{p.referral_target} refs = {p.reward_months} months</span>
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-3">
                  <button onClick={() => togglePromo(p.id)} className="text-slate-400 hover:text-white transition-colors" title={p.is_active ? 'Pause' : 'Activate'} data-testid={`promo-toggle-${p.id}`}>
                    {p.is_active ? <ToggleRight className="w-6 h-6 text-emerald-400" /> : <ToggleLeft className="w-6 h-6" />}
                  </button>
                  <button onClick={() => deletePromo(p.id)} className="text-slate-400 hover:text-red-400 transition-colors" title="Delete" data-testid={`promo-delete-${p.id}`}>
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default AdminPanel;
