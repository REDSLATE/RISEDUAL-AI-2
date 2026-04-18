import React, { useState, useEffect, useCallback } from 'react';
import { Users, Crown, RefreshCw, Gift, FileCode, Database, Key, Lock, Film, ShieldCheck, DollarSign, Activity, Search } from 'lucide-react';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { authFetch } from '../contexts/AuthContext';
import AdminTools from './admin/AdminTools';
import PromoManager from './admin/PromoManager';
import CacheMonitor from './admin/CacheMonitor';
import BrokerOAuthConfig from './admin/BrokerOAuthConfig';
import MediaManager from './admin/MediaManager';
import SecurityAudit from './admin/SecurityAudit';
import WaitlistAdmin from './admin/WaitlistAdmin';
import UsersTab from './admin/UsersTab';
import ProviderHealth from './admin/ProviderHealth';
import KeyVault from './admin/KeyVault';
import HelpSearchInsights from './admin/HelpSearchInsights';
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

  const TABS = [
    { id: 'users', label: 'Users', icon: Users },
    { id: 'providers', label: 'Providers', icon: Activity },
    { id: 'vault', label: 'Vault', icon: Lock },
    { id: 'promos', label: 'Promos', icon: Gift },
    { id: 'broker', label: 'Broker', icon: Key },
    { id: 'cache', label: 'Cache', icon: Database },
    { id: 'media', label: 'Media', icon: Film },
    { id: 'security', label: 'Security', icon: ShieldCheck },
    { id: 'waitlist', label: 'Waitlist', icon: Users },
    { id: 'help-search', label: 'Help Search', icon: Search },
    { id: 'tools', label: 'Tools', icon: FileCode },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="admin-panel">
      <div className="bg-slate-900 rounded-2xl max-w-4xl w-full my-4 border border-slate-400/25">
        <div className="p-4 sm:p-6 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 sm:w-10 sm:h-10 bg-red-600 rounded-xl flex items-center justify-center">
              <Crown className="w-5 h-5 sm:w-6 sm:h-6 text-white" />
            </div>
            <div>
              <h2 className="text-white text-lg sm:text-xl font-bold">Admin Panel</h2>
              <p className="text-slate-300 text-xs sm:text-sm">{users.length} users total</p>
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
        <div className="flex border-b border-slate-400/25 px-4 overflow-x-auto scrollbar-none">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              data-testid={`admin-tab-${t.id}`}
              className={`flex items-center gap-1.5 px-3 py-3 text-xs sm:text-sm font-medium border-b-2 transition-all whitespace-nowrap shrink-0 ${
                tab === t.id ? 'text-[#3DE8D9] border-[#3DE8D9]' : 'text-slate-400 border-transparent hover:text-slate-300'
              }`}
            >
              <t.icon className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'users' ? (
          <UsersTab users={users} filter={filter} setFilter={setFilter} actionLoading={actionLoading} doAction={doAction} />
        ) : tab === 'providers' ? (
          <ProviderHealth />
        ) : tab === 'vault' ? (
          <KeyVault />
        ) : tab === 'promos' ? (
          <PromoManager />
        ) : tab === 'broker' ? (
          <BrokerOAuthConfig />
        ) : tab === 'cache' ? (
          <CacheMonitor />
        ) : tab === 'media' ? (
          <MediaManager />
        ) : tab === 'security' ? (
          <SecurityAudit />
        ) : tab === 'waitlist' ? (
          <WaitlistAdmin />
        ) : tab === 'help-search' ? (
          <HelpSearchInsights />
        ) : (
          <AdminTools />
        )}
      </div>
    </div>
  );
};

export default AdminPanel;
