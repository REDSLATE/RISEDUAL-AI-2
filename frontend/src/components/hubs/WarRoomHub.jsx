import React, { useState, useEffect } from 'react';
import { Swords, TrendingUp, BookOpen, Radio, Sparkles, History } from 'lucide-react';
import AIWarRoom from '../AIWarRoom';
import AIHypothesis from '../AIHypothesis';
import MarketPrediction from '../MarketPrediction';
import AIIntelligence from '../AIIntelligence';
import IconTabBar from './IconTabBar';
import { getRecent, subscribeRecent } from '../../utils/recentTickers';

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
  // When a deep-link navigates into (or within) the War Room, sync the sub-tab.
  useEffect(() => { if (initialTab) setTab(initialTab); }, [initialTab]);
  useEffect(() => subscribeRecent((arr) => setRecent(arr)), []);
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  const onRecentClick = (t) => {
    window.dispatchEvent(new CustomEvent('risedualai-warroom', { detail: t }));
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
