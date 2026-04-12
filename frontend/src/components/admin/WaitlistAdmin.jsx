import React, { useState, useEffect, useCallback } from 'react';
import { Users, Crown, Send, RefreshCw, ArrowUpDown, Trophy, Code, Copy, Check } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api/waitlist`;

const statusColor = (s) => {
  if (s === 'founding') return 'bg-amber-500/15 text-amber-300 border-amber-500/30';
  if (s === 'invited') return 'bg-lime-500/15 text-lime-400 border-lime-500/30';
  if (s === 'active') return 'bg-[#3DE8D9]/15 text-[#3DE8D9] border-[#3DE8D9]/30';
  return 'bg-slate-700 text-slate-400 border-slate-600';
};

const EmbedSnippet = () => {
  const [snippet, setSnippet] = useState(null);
  const [copied, setCopied] = useState(false);
  const [show, setShow] = useState(false);

  const loadSnippet = async () => {
    try {
      const res = await fetch(`${API}/embed/snippet`);
      if (res.ok) setSnippet(await res.json());
    } catch (e) { logger.error('Failed to load embed snippet:', e); }
  };

  const copySnippet = () => {
    if (!snippet) return;
    navigator.clipboard.writeText(snippet.snippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    toast.success('Embed code copied!');
  };

  return (
    <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-3" data-testid="embed-snippet-section">
      <button onClick={() => { setShow(!show); if (!show && !snippet) loadSnippet(); }}
        className="flex items-center gap-2 w-full text-left">
        <Code className="w-4 h-4 text-[#3DE8D9]" />
        <span className="text-white text-xs font-semibold flex-1">Embeddable Widget</span>
        <span className="text-slate-500 text-[10px]">{show ? 'Hide' : 'Show embed code'}</span>
      </button>
      {show && snippet && (
        <div className="mt-3 space-y-2">
          <p className="text-slate-400 text-[10px]">{snippet.instructions}</p>
          <div className="relative">
            <pre className="bg-slate-900 border border-slate-700 rounded-lg p-3 text-[10px] text-slate-300 overflow-x-auto whitespace-pre-wrap break-all">
              {snippet.snippet}
            </pre>
            <button onClick={copySnippet}
              className="absolute top-2 right-2 bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-[10px] text-slate-300 hover:text-white"
              data-testid="copy-embed-snippet">
              {copied ? <Check className="w-3 h-3 inline" /> : <Copy className="w-3 h-3 inline" />}
              {copied ? ' Copied' : ' Copy'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};



const WaitlistAdmin = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sortBy, setSortBy] = useState('priority');
  const [inviting, setInviting] = useState(false);
  const [inviteCount, setInviteCount] = useState(10);

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/admin/list?limit=50&sort_by=${sortBy}`);
      if (res.ok) setData(await res.json());
    } catch { toast.error('Failed to load waitlist'); }
    finally { setLoading(false); }
  }, [sortBy]);

  useEffect(() => { fetchList(); }, [fetchList]);

  const handleInvite = async () => {
    setInviting(true);
    try {
      const res = await authFetch(`${API}/admin/invite`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count: inviteCount }),
      });
      if (res.ok) {
        const d = await res.json();
        toast.success(`Invited ${d.count} users`);
        fetchList();
      }
    } catch { toast.error('Invite failed'); }
    finally { setInviting(false); }
  };

  const handleSelectFounding = async () => {
    if (!window.confirm('Select Founding 100 from invited/active users?')) return;
    try {
      const res = await authFetch(`${API}/admin/select-founding`, { method: 'POST' });
      if (res.ok) {
        const d = await res.json();
        toast.success(`${d.count} Founding Members selected`);
        fetchList();
      }
    } catch { toast.error('Selection failed'); }
  };

  if (loading && !data) {
    return (
      <div className="p-6 flex items-center justify-center">
        <RefreshCw className="w-5 h-5 animate-spin text-[#3DE8D9] mr-2" />
        <span className="text-slate-300 text-sm">Loading waitlist...</span>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4" data-testid="waitlist-admin">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Users className="w-5 h-5 text-[#3DE8D9]" />
          <h3 className="text-white font-semibold">Beta Waitlist</h3>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Button size="sm" variant="outline" onClick={() => setSortBy(sortBy === 'priority' ? 'position' : 'priority')}
            className="bg-slate-800 border-slate-400/30 text-slate-300 text-xs rounded-xl"
            data-testid="waitlist-sort-toggle">
            <ArrowUpDown className="w-3 h-3 mr-1" />
            {sortBy === 'priority' ? 'By Priority' : 'By Signup'}
          </Button>
          <Button size="sm" variant="outline" onClick={fetchList}
            className="bg-slate-800 border-slate-400/30 text-slate-300 text-xs rounded-xl">
            <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>
      </div>

      {/* Stats */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-3 text-center">
            <span className="text-xl font-bold text-white">{data.total}</span>
            <p className="text-slate-400 text-[10px]">Total Signups</p>
          </div>
          <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-3 text-center">
            <span className="text-xl font-bold text-amber-300">{data.waiting}</span>
            <p className="text-slate-400 text-[10px]">Waiting</p>
          </div>
          <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-3 text-center">
            <span className="text-xl font-bold text-lime-400">{data.invited}</span>
            <p className="text-slate-400 text-[10px]">Invited</p>
          </div>
          <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-3 text-center">
            <span className="text-xl font-bold text-[#3DE8D9]">{data.founding_count}</span>
            <p className="text-slate-400 text-[10px]">Founding 100</p>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1">
          <span className="text-slate-400 text-xs">Invite top</span>
          <input type="number" value={inviteCount} onChange={e => setInviteCount(Math.max(1, Math.min(100, +e.target.value)))}
            className="w-14 bg-slate-800 border border-slate-600 text-white text-xs rounded-lg px-2 py-1 text-center"
            data-testid="invite-count-input" />
        </div>
        <Button size="sm" onClick={handleInvite} disabled={inviting}
          className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl text-xs"
          data-testid="invite-btn">
          <Send className="w-3 h-3 mr-1" /> {inviting ? 'Inviting...' : 'Send Invites'}
        </Button>
        <Button size="sm" variant="outline" onClick={handleSelectFounding}
          className="bg-amber-900/20 border-amber-700/30 text-amber-300 rounded-xl text-xs"
          data-testid="select-founding-btn">
          <Crown className="w-3 h-3 mr-1" /> Select Founding 100
        </Button>
      </div>

      {/* Embed Widget Snippet */}
      <EmbedSnippet />

      {/* List */}
      {data && data.entries.length > 0 && (
        <div className="space-y-1 max-h-[400px] overflow-y-auto">
          {data.entries.map(entry => (
            <div key={entry.referral_code} className="flex items-center justify-between px-3 py-2 rounded-lg bg-[#111C30] border border-slate-600/20">
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-slate-500 text-[10px] font-mono w-8 shrink-0">#{entry.position}</span>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-white text-xs font-medium truncate">{entry.name || entry.email}</span>
                    {entry.founding_member && <Crown className="w-3 h-3 text-amber-400 shrink-0" />}
                  </div>
                  <span className="text-slate-500 text-[10px] truncate block">{entry.email}</span>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {entry.referral_count > 0 && (
                  <Badge className="bg-violet-500/15 text-violet-300 border-violet-500/20 text-[10px]">
                    <Trophy className="w-2.5 h-2.5 mr-0.5" /> {entry.referral_count}
                  </Badge>
                )}
                <Badge className={`text-[10px] ${statusColor(entry.status)}`}>
                  {entry.status}
                </Badge>
                <span className="text-slate-500 text-[10px] w-12 text-right">
                  {entry.priority_score}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default WaitlistAdmin;
