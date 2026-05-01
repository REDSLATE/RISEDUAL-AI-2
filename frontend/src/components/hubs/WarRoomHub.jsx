import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Swords, TrendingUp, BookOpen, Radio, Sparkles, History, Share2, Check, Search, Loader2 } from 'lucide-react';
import AIWarRoom from '../AIWarRoom';
import AIHypothesis from '../AIHypothesis';
import MarketPrediction from '../MarketPrediction';
import AIIntelligence from '../AIIntelligence';
import IconTabBar from './IconTabBar';
import { Input } from '../ui/input';
import { Button } from '../ui/button';
import { getRecent, subscribeRecent, addRecent } from '../../utils/recentTickers';
import { authFetch, useAuth } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { toast } from 'sonner';

const MarketSignals = React.lazy(() => import('../MarketSignals'));

const API = `${getApiBase()}/api`;

const TABS = [
  { key: 'adversarial',  label: 'Adversarial AI', icon: Swords,     desc: 'Strategist vs. Auditor adversarial loop' },
  { key: 'prediction',   label: 'Predictions',    icon: TrendingUp, desc: 'Raw AI market verdicts & confidence' },
  { key: 'hypothesis',   label: 'Hypothesis',     icon: BookOpen,   desc: 'Thesis generator — reasoning before the call' },
  { key: 'signals',      label: 'Signals',        icon: Radio,      desc: 'Live technical & flow-based signals' },
  { key: 'intelligence', label: 'Intelligence',   icon: Sparkles,   desc: 'Multi-model AI consensus & memory recall' },
];

// Tabs that are per-symbol and benefit from the unified prefetch.
const PER_SYMBOL_TABS = new Set(['adversarial', 'hypothesis', 'intelligence']);

const errMsgFor = async (res, fallback) => {
  if (res.status === 401) return 'Session expired — please log in again.';
  if (res.status === 403) return 'pro_required';
  if (res.status === 502 || res.status === 504) return 'Server is busy — please try again in a moment.';
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { detail = null; }
    return detail || `Server error (${res.status}). ${fallback || 'Please try again.'}`;
  }
  return null;
};

/**
 * WarRoomHub — consolidated AI command center.
 *
 * Unified Search (Option A): A single ticker entry fires `Promise.all` against the
 * three per-symbol surfaces (War Room, Hypothesis, Intelligence patterns+brief).
 * Each panel renders independently as its slice resolves — one slow upstream
 * does not block the rest.
 */
export default function WarRoomHub({ onSubscribe, onLogin, initialTab }) {
  const { user, isPro } = useAuth();
  const [tab, setTab] = useState(initialTab || 'adversarial');
  const [recent, setRecent] = useState(() => getRecent());
  const [copied, setCopied] = useState(false);
  const [refCode, setRefCode] = useState(null);
  const [refStats, setRefStats] = useState({ completed: 0, rewards: 0 });

  // ── Unified Search state ──────────────────────────────────────────────
  const [queryInput, setQueryInput] = useState('');
  const [activeSymbol, setActiveSymbol] = useState('');
  const [warroom, setWarroom] = useState({ data: null, loading: false, error: '' });
  const [hypothesis, setHypothesis] = useState({ data: null, loading: false, error: '' });
  const [intelligence, setIntelligence] = useState({
    results: { patterns: null, brief: null }, loading: false, error: '',
  });

  // When a deep-link navigates into (or within) the War Room, sync the sub-tab.
  useEffect(() => { if (initialTab) setTab(initialTab); }, [initialTab]);
  useEffect(() => subscribeRecent((arr) => setRecent(arr)), []);

  // Lazily fetch the authenticated user's referral code + ROI stats the first
  // time they open the War Room. Every copied share link carries `?ref=CODE`,
  // and the Share button tooltip surfaces live signup counts — turning it
  // from a one-off action into a habit loop.
  useEffect(() => {
    if (!user || refCode) return;
    let cancelled = false;
    authFetch(`${API}/referral/info`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        if (d.code) setRefCode(d.code);
        setRefStats({
          completed: d.completed_referrals || 0,
          rewards: d.rewards_this_year || 0,
        });
      })
      .catch(() => { /* silent — share still works without ref */ });
    return () => { cancelled = true; };
  }, [user, refCode]);

  // ── Unified fetch orchestration ───────────────────────────────────────
  const runUnifiedSearch = useCallback(async (rawSymbol) => {
    const sym = (rawSymbol || '').trim().toUpperCase();
    if (!sym) return;
    setActiveSymbol(sym);
    addRecent(sym);

    // Reset all panels to loading.
    setWarroom({ data: null, loading: true, error: '' });
    setHypothesis({ data: null, loading: true, error: '' });
    setIntelligence({ results: { patterns: null, brief: null }, loading: true, error: '' });

    const modelParam = 'gpt-5.2'; // default — Pro users can re-run via the Hypothesis tab if they want a different model.

    // Fire each fetch independently so a slow tail doesn't block faster siblings.
    authFetch(`${API}/intelligence/war-room/${sym}`)
      .then(async (res) => {
        const err = await errMsgFor(res);
        if (err) { setWarroom({ data: null, loading: false, error: err }); return; }
        const data = await res.json();
        setWarroom({ data, loading: false, error: '' });
      })
      .catch((e) => setWarroom({ data: null, loading: false, error: e.message || 'Network error' }));

    authFetch(`${API}/hypothesis/${sym}?model=${modelParam}`)
      .then(async (res) => {
        const err = await errMsgFor(res);
        if (err) { setHypothesis({ data: null, loading: false, error: err }); return; }
        const data = await res.json();
        setHypothesis({ data, loading: false, error: '' });
      })
      .catch((e) => setHypothesis({ data: null, loading: false, error: e.message || 'Network error' }));

    // Intelligence is two endpoints (patterns + brief) fetched in parallel.
    Promise.all([
      authFetch(`${API}/intelligence/patterns/${sym}`).then(async (res) => {
        const err = await errMsgFor(res);
        if (err) throw new Error(err);
        return res.json();
      }).catch((e) => ({ __err: e.message || 'Network error' })),
      authFetch(`${API}/intelligence/brief/${sym}`).then(async (res) => {
        const err = await errMsgFor(res);
        if (err) throw new Error(err);
        return res.json();
      }).catch((e) => ({ __err: e.message || 'Network error' })),
    ]).then(([patternsRes, briefRes]) => {
      const patterns = patternsRes?.__err ? null : patternsRes;
      const brief = briefRes?.__err ? null : briefRes;
      // Surface the first non-pro-required error we see (pro_required suppressed
      // here because Intelligence is currently free-tier accessible).
      const firstErr = (patternsRes?.__err && patternsRes.__err !== 'pro_required' ? patternsRes.__err : '')
        || (briefRes?.__err && briefRes.__err !== 'pro_required' ? briefRes.__err : '');
      setIntelligence({
        results: { patterns, brief },
        loading: false,
        error: (!patterns && !brief) ? firstErr : '',
      });
    });
  }, []);

  const onSubmit = (e) => {
    e?.preventDefault();
    runUnifiedSearch(queryInput);
  };

  // Bridge: existing surfaces dispatch `risedualai-warroom` deep-links with a
  // ticker payload. We intercept here so the unified search owns routing.
  useEffect(() => {
    const handler = (e) => {
      const t = (e?.detail || '').toString().trim().toUpperCase();
      if (!t) return;
      setQueryInput(t);
      runUnifiedSearch(t);
    };
    window.addEventListener('risedualai-warroom', handler);
    return () => window.removeEventListener('risedualai-warroom', handler);
  }, [runUnifiedSearch]);

  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  const onRecentClick = (t) => {
    setQueryInput(t);
    runUnifiedSearch(t);
  };

  const currentTicker = activeSymbol || recent[0] || null;
  const shareUrl = currentTicker
    ? `${window.location.origin}/api/share/${currentTicker}${refCode ? `?ref=${refCode}` : ''}`
    : null;

  const onShare = async () => {
    if (!currentTicker || !shareUrl) return;
    const title = `${currentTicker} · RISEDUAL AI War Room`;
    if (navigator.share) {
      try {
        await navigator.share({ title, url: shareUrl });
        return;
      } catch (err) {
        console.debug('[war-room] native share dismissed, falling back to clipboard', err?.message);
      }
    }
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      toast.success(`Link to ${currentTicker} War Room copied`, {
        description: 'Paste on X, Slack, iMessage, LinkedIn… the preview renders with live price.',
        duration: 4000,
      });
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Could not copy share link');
    }
  };

  const showUnifiedSearch = PER_SYMBOL_TABS.has(tab);
  const anyLoading = warroom.loading || hypothesis.loading || intelligence.loading;

  return (
    <div data-testid="war-room-hub" className="animate-enter">
      {/* Big marquee heading — distinct from the sub-tab strip */}
      <div className="mb-4 flex items-center gap-2 flex-wrap">
        <div className="w-7 h-7 rounded-lg bg-orange-500/10 border border-orange-500/30 flex items-center justify-center">
          <Swords className="w-4 h-4 text-orange-400" />
        </div>
        <h1 className="text-white text-xl sm:text-2xl font-bold tracking-tight">AI War Room</h1>
        <span className="ml-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-300 border border-orange-500/30">v2</span>
        {currentTicker && (
          <button
            onClick={onShare}
            className="ml-1 inline-flex items-center gap-1.5 text-[11px] font-semibold px-2 py-1 rounded-full bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/30 hover:bg-[#3DE8D9]/20 hover:text-[#7AEEE0] transition-colors"
            title={
              refStats.completed > 0
                ? `Share ${currentTicker} War Room — ${refStats.completed} signup${refStats.completed === 1 ? '' : 's'} via your links so far. Previews on X, Slack, iMessage, LinkedIn, WhatsApp, Discord, Telegram…`
                : `Share ${currentTicker} War Room — live preview on X, Slack, iMessage, LinkedIn, WhatsApp, Discord, Telegram…`
            }
            data-testid="warroom-share-btn"
          >
            {copied ? <Check className="w-3 h-3" /> : <Share2 className="w-3 h-3" />}
            <span>{copied ? 'Copied' : `Share ${currentTicker}`}</span>
            {refStats.completed > 0 && !copied && (
              <span
                className="ml-0.5 inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full bg-lime-400/20 text-lime-300 text-[9px] font-bold tabular-nums border border-lime-400/30"
                data-testid="warroom-share-roi-badge"
              >
                {refStats.completed}
              </span>
            )}
          </button>
        )}
        {recent.length > 0 && (
          <div className="flex items-center gap-1.5 ml-auto" data-testid="warroom-recent-strip">
            <span className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold inline-flex items-center gap-1">
              <History className="w-3 h-3" /> Recent
            </span>
            {recent.map((t) => (
              <button
                key={t}
                onClick={() => onRecentClick(t)}
                className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-orange-500/10 text-orange-300 border border-orange-500/30 hover:bg-orange-500/20 hover:text-orange-200 transition-colors tabular-nums"
                title={`Re-run ${t} across all War Room tabs`}
                data-testid={`warroom-recent-${t}`}
              >
                {t}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Unified Search — visible only on per-symbol tabs */}
      {showUnifiedSearch && (
        <form
          onSubmit={onSubmit}
          className="mb-4 flex flex-col sm:flex-row gap-3"
          data-testid="warroom-unified-search-form"
        >
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input
              placeholder="One search → War Room + Hypothesis + Intelligence (AAPL, TSLA, NVDA...)"
              value={queryInput}
              onChange={(e) => setQueryInput(e.target.value.toUpperCase())}
              className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
              data-testid="warroom-unified-search-input"
            />
          </div>
          <Button
            type="submit"
            disabled={anyLoading || !queryInput.trim()}
            className="bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-400 hover:to-amber-400 text-white rounded-xl px-6"
            data-testid="warroom-unified-search-submit"
          >
            {anyLoading ? (
              <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Running…</>
            ) : (
              <>Run All</>
            )}
          </Button>
        </form>
      )}

      <IconTabBar
        tabs={TABS} value={tab} onChange={setTab}
        enabled
        accent="text-orange-300"
        accentHex="#fdba74"
        testIdPrefix="warroom-tab"
        legendTitle="War Room Legend"
      />

      <React.Suspense fallback={fallback}>
        <div className="animate-enter">
          {tab === 'adversarial'  && (
            <AIWarRoom
              onSubscribe={onSubscribe}
              onLogin={onLogin}
              prefetched={{
                symbol: activeSymbol,
                data: warroom.data,
                loading: warroom.loading,
                error: warroom.error,
              }}
            />
          )}
          {tab === 'prediction'   && <MarketPrediction />}
          {tab === 'hypothesis'   && (
            <AIHypothesis
              onSubscribe={onSubscribe}
              onLogin={onLogin}
              prefetched={{
                symbol: activeSymbol,
                data: hypothesis.data,
                loading: hypothesis.loading,
                error: hypothesis.error,
              }}
            />
          )}
          {tab === 'signals'      && <MarketSignals onSubscribe={onSubscribe} />}
          {tab === 'intelligence' && (
            <AIIntelligence
              onSubscribe={onSubscribe}
              prefetched={{
                symbol: activeSymbol,
                results: intelligence.results,
                loading: intelligence.loading,
                error: intelligence.error,
              }}
            />
          )}
        </div>
      </React.Suspense>
    </div>
  );
}
