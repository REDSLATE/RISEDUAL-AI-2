import React from 'react';
import { Shield, ShieldOff, UserCheck, UserX, Search } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Input } from '../ui/input';

const UsersTab = ({ users, filter, setFilter, actionLoading, doAction }) => {
  const filtered = users.filter(u =>
    u.name?.toLowerCase().includes(filter.toLowerCase()) ||
    u.email?.toLowerCase().includes(filter.toLowerCase())
  );

  return (
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
                        <Button size="sm" variant="outline" className="text-[10px] px-2 py-1 h-7 bg-orange-800 text-orange-400 border-orange-700/50 hover:bg-red-500/30"
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
  );
};

export default UsersTab;
