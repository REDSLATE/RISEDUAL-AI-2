import React, { useState, useEffect } from 'react';
import { Swords, TrendingUp, BookOpen, Radio, Sparkles, History, Share2, Check } from 'lucide-react';
import AIWarRoom from '../AIWarRoom';
import AIHypothesis from '../AIHypothesis';
import MarketPrediction from '../MarketPrediction';
import AIIntelligence from '../AIIntelligence';
import IconTabBar from './IconTabBar';
import { getRecent, subscribeRecent } from '../../utils/recentTickers';
import { toast } from 'sonner';

const MarketSignals = React.lazy(() => import('../MarketSignals'));

const TABS = [
  { key: 'adversarial',  label: 'Adversarial AI', icon: Swords,     desc: 'Strategist vs. Auditor adversarial loop' },
  { key: 'prediction',   label: 'Predictions',    icon: TrendingUp, desc: 'Raw AI market verdicts & confidence' },
  { key: 'hypothesis',   label: 'Hypothesis',     icon: BookOpen,   desc: 'Thesis generator — reasoning before the call' },
  { key: 'signals',      label: 'Signals',        icon: Radio,      desc: 'Live technical & flow-based signals' },
  { key: 'intelligence', label: 'Intelligence',   icon: Sparkles,   desc: 'Multi-model AI consensus & memory recall' },
];

/**
 * WarRoomHub — consolidated AI command center.
 *
 * Merges what used to be spread across:
 *   - Dashboard > AI War Room      → Adversarial AI
 *   - Research  > Predictions      → Predictions
 *   - Research  > Hypothesis       → Hypothesis
 *   - Research  > Signals          → Signals
 *   - Dashboard > AI Intelligence  → Intelligence
 */
export default function WarRoomHub({ onSubscribe, onLogin, initialTab }) {
  const [tab, setTab] = useState(initialTab || 'adversarial');
  const [recent, setRecent] = useState(() => getRecent());
  const [copied, setCopied] = useState(false);
  // When a deep-link navigates into (or within) the War Room, sync the sub-tab.
  useEffect(() => { if (initialTab) setTab(initialTab); }, [initialTab]);
  useEffect(() => subscribeRecent((arr) => setRecent(arr)), []);
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  const onRecentClick = (t) => {
    window.dispatchEvent(new CustomEvent('risedualai-warroom', { detail: t }));
  };

  const currentTicker = recent[0] || null;
  const shareUrl = currentTicker
    ? `${window.location.origin}/api/share/${currentTicker}`
    : null;

  const onShare = async () => {
    if (!currentTicker || !shareUrl) return;
    const title = `${currentTicker} · RISEDUAL AI War Room`;
    // Prefer the native Web Share API on mobile (iOS/Android) — gives the
    // full share sheet (X, iMessage, WhatsApp, Mail, Slack, Signal, etc.).
    if (navigator.share) {
      try {
        await navigator.share({ title, url: shareUrl });
        return;
      } catch {
        /* user-cancelled or unsupported → fall through to clipboard */
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
            title={`Share ${currentTicker} War Room — live preview on X, Slack, iMessage, LinkedIn, WhatsApp, Discord, Telegram…`}
            data-testid="warroom-share-btn"
          >
            {copied ? <Check className="w-3 h-3" /> : <Share2 className="w-3 h-3" />}
            <span>{copied ? 'Copied' : `Share ${currentTicker}`}</span>
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
                title={`Re-run ${t} in this War Room tab`}
                data-testid={`warroom-recent-${t}`}
              >
                {t}
              </button>
            ))}
          </div>
        )}
      </div>

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
          {tab === 'adversarial'  && <AIWarRoom onSubscribe={onSubscribe} onLogin={onLogin} />}
          {tab === 'prediction'   && <MarketPrediction />}
          {tab === 'hypothesis'   && <AIHypothesis onSubscribe={onSubscribe} onLogin={onLogin} />}
          {tab === 'signals'      && <MarketSignals onSubscribe={onSubscribe} />}
          {tab === 'intelligence' && <AIIntelligence onSubscribe={onSubscribe} />}
        </div>
      </React.Suspense>
    </div>
  );
}
