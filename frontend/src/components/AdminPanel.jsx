import React, { useState, useEffect, useCallback } from 'react';
import { Users, Shield, ShieldOff, Crown, UserCheck, UserX, RefreshCw, Search, Gift, FileCode, Database, Key } from 'lucide-react';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { toast } from './ui/sonner';
import { authFetch } from '../contexts/AuthContext';
import AdminTools from './admin/AdminTools';
import PromoManager from './admin/PromoManager';
import CacheMonitor from './admin/CacheMonitor';
import BrokerOAuthConfig from './admin/BrokerOAuthConfig';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

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
      logger.error('Error fetching users:', e);
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
        toast.error(d.detail || 'Action failed');
      }
    } catch (e) {
      toast.error('Action failed');
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
    { id: 'broker', label: 'Broker', icon: Key },
    { id: 'cache', label: 'Cache', icon: Database },
    { id: 'tools', label: 'Tools', icon: FileCode },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="admin-panel">
      <div className="bg-slate-900 rounded-2xl max-w-4xl w-full my-4 border border-slate-400/25">
        <div className="p-6 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-600 rounded-xl flex items-center justify-center">
              <Crown className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-white text-xl font-bold">Admin Panel</h2>
              <p className="text-slate-300 text-sm">{users.length} users total</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={fetchUsers} className="bg-slate-800 border-slate-400/30 text-white">
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>
            <button onClick={onClose} className="text-slate-400 hover:text-white text-xl px-2">x</button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-400/25 px-4">
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              data-testid={`admin-tab-${t.id}`}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-all ${
                tab === t.id ? 'text-[#3DE8D9] border-[#3DE8D9]' : 'text-slate-400 border-transparent hover:text-slate-300'
              }`}
            >
              <t.icon className="w-4 h-4" />
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'users' ? (
          <>
            <div className="p-4 border-b border-slate-400/25">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
                <Input placeholder="Search users..." value={filter} onChange={e => setFilter(e.target.value)}
                  className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl" />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="admin-users-table">
                <thead>
                  <tr className="border-b border-slate-400/30/40">
                    <th className="text-left text-slate-300 text-xs font-medium px-4 py-3">User</th>
                    <th className="text-left text-slate-300 text-xs font-medium px-4 py-3">Role</th>
                    <th className="text-left text-slate-300 text-xs font-medium px-4 py-3">Plan</th>
                    <th className="text-left text-slate-300 text-xs font-medium px-4 py-3">Status</th>
                    <th className="text-right text-slate-300 text-xs font-medium px-4 py-3">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map(u => (
                    <tr key={u._id} className="border-b border-slate-600/30/40 hover:bg-slate-700/35">
                      <td className="px-4 py-3">
                        <p className="text-white font-medium">{u.name}</p>
                        <p className="text-slate-300 text-xs">{u.email}</p>
                      </td>
                      <td className="px-4 py-3">
                        <Badge className={`text-[10px] ${
                          u.role === 'owner' ? 'bg-orange-700 text-orange-400 border-orange-700' :
                          u.role === 'admin' ? 'bg-blue-900/40 text-blue-400 border-blue-800' :
                          'bg-slate-700 text-slate-400'
                        }`}>{u.role}</Badge>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-semibold ${u.subscription_status === 'pro' ? 'text-[#3DE8D9]' : u.subscription_status === 'trial' ? 'text-lime-400' : 'text-slate-400'}`}>
                          {u.subscription_status === 'pro' ? 'PRO' : u.subscription_status === 'trial' ? 'TRIAL' : 'FREE'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs ${u.is_active !== false ? 'text-lime-400' : 'text-orange-400'}`}>
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
                              <Button size="sm" variant="outline" className="text-[10px] px-2 py-1 h-7 bg-orange-800 text-orange-400 border-orange-700/50 hover:bg-red-800/40"
                                disabled={actionLoading === `${u._id}-deactivate`}
                                onClick={() => doAction(u._id, 'deactivate')}>
                                <UserX className="w-3 h-3 mr-1" /> Disable
                              </Button>
                            ) : (
                              <Button size="sm" variant="outline" className="text-[10px] px-2 py-1 h-7 bg-lime-700 text-lime-400 border-lime-700/50 hover:bg-emerald-800/40"
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
        ) : tab === 'promos' ? (
          <PromoManager />
        ) : tab === 'broker' ? (
          <BrokerOAuthConfig />
        ) : tab === 'cache' ? (
          <CacheMonitor />
        ) : (
          <AdminTools />
        )}
      </div>
    </div>
  );
};

export default AdminPanel;
