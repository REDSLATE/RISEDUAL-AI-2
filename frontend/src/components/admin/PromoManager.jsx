import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Gift, Calendar, Trash2, ToggleLeft, ToggleRight, RefreshCw } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

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
      logger.error('Error fetching promos:', e);
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
      logger.error('Create promo error:', e);
    } finally {
      setCreating(false);
    }
  };

  const togglePromo = async (id) => {
    try {
      await authFetch(`${API}/promo/${id}/toggle`, { method: 'PUT' });
      fetchPromos();
    } catch (e) {
      logger.error('Toggle promo error:', e);
    }
  };

  const deletePromo = async (id) => {
    try {
      await authFetch(`${API}/promo/${id}`, { method: 'DELETE' });
      fetchPromos();
    } catch (e) {
      logger.error('Delete promo error:', e);
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

      {showCreate && (
        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-4 space-y-3" data-testid="promo-create-form">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-slate-400 text-[10px] block mb-1">Title</label>
              <Input placeholder="e.g., Launch Week Special" value={form.title} onChange={e => setForm({ ...form, title: e.target.value })}
                className="bg-slate-900 border-slate-600 text-white text-sm rounded-xl h-9" data-testid="promo-title-input" />
            </div>
            <div>
              <label className="text-slate-400 text-[10px] block mb-1">Message</label>
              <Input placeholder="e.g., Refer 3 friends, get 2 months free!" value={form.message} onChange={e => setForm({ ...form, message: e.target.value })}
                className="bg-slate-900 border-slate-600 text-white text-sm rounded-xl h-9" data-testid="promo-message-input" />
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

export default PromoManager;
