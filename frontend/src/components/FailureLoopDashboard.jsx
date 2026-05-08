import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { X, AlertTriangle, TrendingDown, TrendingUp, Clock, Tag, ChevronRight, Plus, Check, BarChart3 } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { toast } from './ui/sonner';
import { authFetch } from '../contexts/AuthContext';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const OUTCOME_COLORS = {
  win: { bg: 'bg-lime-500/10', border: 'border-lime-500/30', text: 'text-lime-400' },
  loss: { bg: 'bg-red-500/10', border: 'border-red-500/30', text: 'text-red-400' },
  mixed: { bg: 'bg-amber-500/10', border: 'border-amber-500/30', text: 'text-amber-400' },
  invalid: { bg: 'bg-slate-500/10', border: 'border-slate-500/30', text: 'text-slate-400' },
  open: { bg: 'bg-[#3DE8D9]/10', border: 'border-[#3DE8D9]/30', text: 'text-[#3DE8D9]' },
};

const FailureLoopDashboard = ({ onClose }) => {
  const [ideas, setIdeas] = useState([]);
  const [patterns, setPatterns] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [timeline, setTimeline] = useState([]);
  const [tab, setTab] = useState('overview');
  const [showCreate, setShowCreate] = useState(false);
  const [reviewTarget, setReviewTarget] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [ideasRes, patternsRes, warningsRes, timelineRes] = await Promise.all([
        authFetch(`${API}/failure-loop/ideas?limit=30`),
        authFetch(`${API}/failure-loop/patterns`),
        authFetch(`${API}/failure-loop/warnings`),
        authFetch(`${API}/failure-loop/timeline?limit=20`),
      ]);
      if (ideasRes.ok) { const d = await ideasRes.json(); setIdeas(d.ideas || []); }
      if (patternsRes.ok) { const d = await patternsRes.json(); setPatterns(d.patterns || []); }
      if (warningsRes.ok) { const d = await warningsRes.json(); setWarnings(d.warnings || []); }
      if (timelineRes.ok) { const d = await timelineRes.json(); setTimeline(d.events || []); }
    } catch (e) {
      logger.warn('Failure loop fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Derived counts — memoized so a keystroke in a modal doesn't re-traverse
  // the ideas array four times per render.
  const stats = useMemo(() => ({
    total: ideas.length,
    wins: ideas.filter(i => i.status === 'win').length,
    losses: ideas.filter(i => i.status === 'loss').length,
    open: ideas.filter(i => i.status === 'open').length,
    openList: ideas.filter(i => i.status === 'open').slice(0, 5),
  }), [ideas]);
  const topTag = patterns[0]?.tag || '--';
  const totalIdeas = stats.total;
  const wins = stats.wins;
  const losses = stats.losses;
  const openIdeas = stats.open;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="failure-loop-dashboard">
      <div className="bg-slate-900 rounded-2xl max-w-4xl w-full my-4 border border-slate-400/25">
        {/* Header */}
        <div className="p-5 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-red-500/20 rounded-xl flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-400" />
            </div>
            <div>
              <h2 className="text-white text-lg font-bold">Failure Loop</h2>
              <p className="text-slate-400 text-[10px]">Review mistakes so the AI warns instead of repeating weak logic</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => setShowCreate(true)} className="bg-[#3DE8D9]/15 text-[#3DE8D9] border border-[#3DE8D9]/30 h-8 text-xs" data-testid="fl-create-idea-btn">
              <Plus className="w-3.5 h-3.5 mr-1" /> Log Idea
            </Button>
            <button onClick={onClose} className="text-slate-400 hover:text-white" data-testid="fl-close"><X className="w-5 h-5" /></button>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-5 pb-0">
          <StatCard label="Total Ideas" value={totalIdeas} color="text-white" />
          <StatCard label="Wins" value={wins} color="text-lime-400" />
          <StatCard label="Losses" value={losses} color="text-red-400" />
          <StatCard label="Top Failure Tag" value={topTag} color="text-amber-400" small />
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-400/25 px-5 mt-4">
          {[
            { id: 'overview', label: 'Overview' },
            { id: 'ideas', label: `Ideas (${totalIdeas})` },
            { id: 'patterns', label: `Patterns (${patterns.length})` },
            { id: 'timeline', label: 'Timeline' },
          ].map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`px-3 py-3 text-xs font-medium border-b-2 transition-all ${
                tab === t.id ? 'text-red-400 border-red-400' : 'text-slate-400 border-transparent hover:text-slate-300'
              }`}
              data-testid={`fl-tab-${t.id}`}
            >{t.label}</button>
          ))}
        </div>

        <div className="p-5">
          {loading ? (
            <div className="space-y-3">{[1,2,3].map(i => <div key={`sk-${i}`} className="h-14 bg-slate-800/50 rounded-xl animate-pulse" />)}</div>
          ) : tab === 'overview' ? (
            <OverviewTab patterns={patterns} warnings={warnings} openIdeas={openIdeas} openList={stats.openList} onReview={setReviewTarget} />
          ) : tab === 'ideas' ? (
            <IdeasTab ideas={ideas} onReview={setReviewTarget} />
          ) : tab === 'patterns' ? (
            <PatternsTab patterns={patterns} />
          ) : (
            <TimelineTab timeline={timeline} />
          )}
        </div>

        {showCreate && <CreateIdeaModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); fetchAll(); }} />}
        {reviewTarget && <ReviewModal idea={reviewTarget} onClose={() => setReviewTarget(null)} onReviewed={() => { setReviewTarget(null); fetchAll(); }} />}
      </div>
    </div>
  );
};

const StatCard = ({ label, value, color, small }) => (
  <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-600/20">
    <p className="text-slate-400 text-[9px] uppercase tracking-wider">{label}</p>
    <p className={`${small ? 'text-sm' : 'text-xl'} font-bold ${color} mt-1`}>{value}</p>
  </div>
);

const OverviewTab = ({ patterns, warnings, openIdeas, openList, onReview }) => (
  <div className="grid lg:grid-cols-2 gap-4">
    {/* Warnings */}
    <div>
      <h3 className="text-white text-xs font-semibold mb-3">Active Warnings</h3>
      {warnings.length === 0 ? (
        <p className="text-slate-500 text-[10px] py-4">No warnings yet. Review trade outcomes to build patterns.</p>
      ) : (
        <div className="space-y-2" data-testid="fl-warnings">
          {warnings.map((w, i) => (
            <div key={`warn-${i}`} className="bg-amber-900/15 border border-amber-700/30 rounded-xl p-3 flex items-start gap-2">
              <AlertTriangle className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
              <p className="text-amber-300/90 text-[10px] leading-relaxed">{w}</p>
            </div>
          ))}
        </div>
      )}
    </div>

    {/* Top patterns */}
    <div>
      <h3 className="text-white text-xs font-semibold mb-3">Top Failure Patterns</h3>
      {patterns.length === 0 ? (
        <p className="text-slate-500 text-[10px] py-4">No patterns yet.</p>
      ) : (
        <div className="space-y-2" data-testid="fl-top-patterns">
          {patterns.slice(0, 5).map(p => (
            <div key={p.tag} className="flex items-center justify-between bg-slate-800/40 rounded-xl px-4 py-2.5 border border-slate-600/20">
              <div className="flex items-center gap-2">
                <Tag className="w-3.5 h-3.5 text-red-400" />
                <span className="text-white text-xs font-medium">{p.tag.replace(/_/g, ' ')}</span>
              </div>
              <div className="text-right">
                <span className="text-red-400 text-xs font-bold">{p.count}x</span>
                <span className="text-slate-500 text-[10px] ml-2">avg {p.avg_pnl < 0 ? '' : '+'}{p.avg_pnl}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>

    {/* Open ideas needing review */}
    {openIdeas > 0 && (
      <div className="lg:col-span-2">
        <h3 className="text-white text-xs font-semibold mb-3">Open Ideas — Awaiting Review ({openIdeas})</h3>
        <div className="space-y-2">
          {openList.map(idea => (
            <IdeaRow key={idea.idea_id} idea={idea} onReview={onReview} />
          ))}
        </div>
      </div>
    )}
  </div>
);

const IdeasTab = ({ ideas, onReview }) => (
  <div className="space-y-2" data-testid="fl-ideas-list">
    {ideas.length === 0 ? (
      <p className="text-slate-500 text-xs text-center py-8">No trade ideas logged yet. Click "Log Idea" to start.</p>
    ) : (
      ideas.map(idea => <IdeaRow key={idea.idea_id} idea={idea} onReview={onReview} />)
    )}
  </div>
);

const IdeaRow = ({ idea, onReview }) => {
  const oc = OUTCOME_COLORS[idea.status] || OUTCOME_COLORS.open;
  return (
    <div className={`flex items-center justify-between ${oc.bg} border ${oc.border} rounded-xl px-4 py-3`} data-testid={`fl-idea-${idea.idea_id?.slice(-6)}`}>
      <div className="flex items-center gap-3 min-w-0">
        {idea.direction === 'long' ? <TrendingUp className="w-4 h-4 text-lime-400 shrink-0" /> : <TrendingDown className="w-4 h-4 text-red-400 shrink-0" />}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-white text-xs font-bold">{idea.symbol}</span>
            <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-full ${oc.bg} ${oc.text}`}>{idea.status}</span>
            {idea.pnl !== undefined && idea.pnl !== null && idea.status !== 'open' && (
              <span className={`text-[10px] font-bold ${idea.pnl >= 0 ? 'text-lime-400' : 'text-red-400'}`}>
                {idea.pnl >= 0 ? '+' : ''}{idea.pnl}
              </span>
            )}
          </div>
          <p className="text-slate-400 text-[10px] truncate">{idea.thesis}</p>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="text-slate-500 text-[9px]">{idea.created_at?.split('T')[0]}</span>
        {idea.status === 'open' && (
          <button onClick={() => onReview(idea)} className="text-amber-400 hover:text-amber-300 p-1" data-testid={`fl-review-${idea.idea_id?.slice(-6)}`}>
            <ChevronRight className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
};

const PatternsTab = ({ patterns }) => (
  <div data-testid="fl-patterns-full">
    {patterns.length === 0 ? (
      <p className="text-slate-500 text-xs text-center py-8">No failure patterns detected yet.</p>
    ) : (
      <div className="space-y-3">
        {patterns.map(p => (
          <div key={p.tag} className="bg-slate-800/40 rounded-xl p-4 border border-slate-600/20">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-red-400" />
                <span className="text-white text-sm font-bold">{p.tag.replace(/_/g, ' ')}</span>
              </div>
              <span className="text-red-400 text-sm font-bold">{p.count} occurrences</span>
            </div>
            <div className="flex items-center gap-4 text-[10px]">
              <span className="text-slate-400">Avg P&L: <span className={p.avg_pnl < 0 ? 'text-red-400 font-bold' : 'text-lime-400 font-bold'}>${p.avg_pnl}</span></span>
              <span className="text-slate-400">Symbols: <span className="text-white">{(p.symbols || []).join(', ') || '--'}</span></span>
            </div>
          </div>
        ))}
      </div>
    )}
  </div>
);

const TimelineTab = ({ timeline }) => (
  <div data-testid="fl-timeline">
    {timeline.length === 0 ? (
      <p className="text-slate-500 text-xs text-center py-8">No events yet.</p>
    ) : (
      <div className="space-y-2">
        {timeline.map((e, i) => (
          <div key={`evt-${i}`} className="border-l-2 border-[#3DE8D9]/40 pl-3 py-2">
            <div className="flex items-center gap-2">
              <Clock className="w-3 h-3 text-[#3DE8D9]" />
              <span className="text-white text-[10px] font-bold">{e.type?.replace(/_/g, ' ')}</span>
              <span className="text-slate-500 text-[9px]">{e.timestamp?.split('T')[0]}</span>
            </div>
            {e.idea_id && <p className="text-slate-500 text-[9px] mt-0.5">Idea: {e.idea_id.slice(0, 8)}...</p>}
          </div>
        ))}
      </div>
    )}
  </div>
);

const CreateIdeaModal = ({ onClose, onCreated }) => {
  const [symbol, setSymbol] = useState('');
  const [direction, setDirection] = useState('long');
  const [thesis, setThesis] = useState('');
  const [confidence, setConfidence] = useState(0.5);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!symbol.trim() || !thesis.trim()) { toast.error('Symbol and thesis required'); return; }
    setSubmitting(true);
    try {
      const res = await authFetch(`${API}/failure-loop/ideas`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: symbol.trim(), direction, thesis: thesis.trim(), confidence, source: 'user', tags: [] }),
      });
      if (res.ok) { toast.success('Trade idea logged'); onCreated(); }
      else { const d = await res.json(); toast.error(d.detail || 'Failed'); }
    } catch { toast.error('Error'); }
    finally { setSubmitting(false); }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-[60] flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-slate-800 rounded-xl max-w-sm w-full p-5 border border-slate-600/30" onClick={e => e.stopPropagation()} data-testid="fl-create-modal">
        <h3 className="text-white text-sm font-bold mb-4">Log Trade Idea</h3>
        <div className="space-y-3">
          <Input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="Symbol (e.g. TSLA)" className="bg-slate-900 border-slate-600 text-white text-xs h-9" data-testid="fl-create-symbol" />
          <div className="flex gap-2">
            {['long', 'short'].map(d => (
              <button key={d} onClick={() => setDirection(d)} className={`flex-1 py-2 rounded-lg text-xs font-bold transition-colors ${direction === d ? (d === 'long' ? 'bg-lime-500/20 text-lime-400 border border-lime-500/30' : 'bg-red-500/20 text-red-400 border border-red-500/30') : 'bg-slate-700 text-slate-400 border border-slate-600/30'}`} data-testid={`fl-dir-${d}`}>
                {d.toUpperCase()}
              </button>
            ))}
          </div>
          <textarea value={thesis} onChange={e => setThesis(e.target.value)} placeholder="Thesis — why this trade?" rows={3} className="w-full bg-slate-900 border border-slate-600 text-white text-xs rounded-lg p-2.5 resize-none" data-testid="fl-create-thesis" />
          <div>
            <label className="text-slate-400 text-[10px]">Confidence: {(confidence * 100).toFixed(0)}%</label>
            <input type="range" min="0" max="1" step="0.05" value={confidence} onChange={e => setConfidence(parseFloat(e.target.value))} className="w-full" />
          </div>
          <Button onClick={submit} disabled={submitting} className="w-full bg-[#3DE8D9] text-slate-900 font-bold h-9 text-xs" data-testid="fl-create-submit">
            {submitting ? 'Saving...' : 'Log Idea'}
          </Button>
        </div>
      </div>
    </div>
  );
};

const ReviewModal = ({ idea, onClose, onReviewed }) => {
  const [outcome, setOutcome] = useState('loss');
  const [pnl, setPnl] = useState('');
  const [selectedTags, setSelectedTags] = useState([]);
  const [notes, setNotes] = useState('');
  const [approvedForLearning, setApprovedForLearning] = useState(true);
  const [reasonTags, setReasonTags] = useState([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    authFetch(`${API}/failure-loop/reason-tags`).then(r => r.json()).then(d => setReasonTags(d.tags || [])).catch(() => {});
  }, []);

  const toggleTag = (tag) => setSelectedTags(prev => prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]);

  const submit = async () => {
    setSubmitting(true);
    try {
      const res = await authFetch(`${API}/failure-loop/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idea_id: idea.idea_id, outcome, pnl: parseFloat(pnl) || 0,
          reason_tags: selectedTags, notes, approved_for_learning: approvedForLearning,
        }),
      });
      if (res.ok) { toast.success('Review saved'); onReviewed(); }
      else { const d = await res.json(); toast.error(d.detail || 'Failed'); }
    } catch { toast.error('Error'); }
    finally { setSubmitting(false); }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-[60] flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-slate-800 rounded-xl max-w-md w-full p-5 border border-slate-600/30" onClick={e => e.stopPropagation()} data-testid="fl-review-modal">
        <h3 className="text-white text-sm font-bold mb-1">Review: {idea.symbol} {idea.direction?.toUpperCase()}</h3>
        <p className="text-slate-400 text-[10px] mb-4 truncate">{idea.thesis}</p>

        <div className="space-y-3">
          {/* Outcome */}
          <div className="flex gap-2">
            {['win', 'loss', 'mixed', 'invalid'].map(o => {
              const oc = OUTCOME_COLORS[o];
              return (
                <button key={o} onClick={() => setOutcome(o)} className={`flex-1 py-2 rounded-lg text-[10px] font-bold transition-colors border ${outcome === o ? `${oc.bg} ${oc.text} ${oc.border}` : 'bg-slate-700 text-slate-400 border-slate-600/30'}`} data-testid={`fl-outcome-${o}`}>
                  {o.toUpperCase()}
                </button>
              );
            })}
          </div>

          {/* P&L */}
          <Input value={pnl} onChange={e => setPnl(e.target.value)} placeholder="P&L (e.g. -450 or 320)" type="number" className="bg-slate-900 border-slate-600 text-white text-xs h-9" data-testid="fl-review-pnl" />

          {/* Reason Tags */}
          <div>
            <label className="text-slate-400 text-[10px] block mb-1.5">Reason Tags</label>
            <div className="flex flex-wrap gap-1.5" data-testid="fl-reason-tags">
              {reasonTags.map(tag => (
                <button key={tag} onClick={() => toggleTag(tag)} className={`px-2 py-1 rounded-lg text-[9px] font-bold transition-colors border ${selectedTags.includes(tag) ? 'bg-red-500/20 text-red-400 border-red-500/30' : 'bg-slate-700 text-slate-400 border-slate-600/30'}`}>
                  {tag.replace(/_/g, ' ')}
                </button>
              ))}
            </div>
          </div>

          {/* Notes */}
          <textarea value={notes} onChange={e => setNotes(e.target.value)} placeholder="Review notes (optional)" rows={2} className="w-full bg-slate-900 border border-slate-600 text-white text-xs rounded-lg p-2.5 resize-none" />

          {/* Approved for learning */}
          <label className="flex items-center gap-2 cursor-pointer" data-testid="fl-approve-learning">
            <input type="checkbox" checked={approvedForLearning} onChange={e => setApprovedForLearning(e.target.checked)} className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-[#3DE8D9]" />
            <span className="text-slate-300 text-[10px]">Approve for AI learning — warnings will be surfaced in future chats</span>
          </label>

          <Button onClick={submit} disabled={submitting} className="w-full bg-amber-500 hover:bg-amber-400 text-slate-900 font-bold h-9 text-xs" data-testid="fl-review-submit">
            {submitting ? 'Saving...' : 'Save Review'}
          </Button>
        </div>
      </div>
    </div>
  );
};

export default FailureLoopDashboard;
