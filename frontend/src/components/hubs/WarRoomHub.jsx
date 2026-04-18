import React, { useState, useEffect } from 'react';
import { Swords, TrendingUp, BookOpen, Radio, Sparkles } from 'lucide-react';
import AIWarRoom from '../AIWarRoom';
import AIHypothesis from '../AIHypothesis';
import MarketPrediction from '../MarketPrediction';
import AIIntelligence from '../AIIntelligence';
import IconTabBar from './IconTabBar';

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
  // When a deep-link navigates into (or within) the War Room, sync the sub-tab.
  useEffect(() => { if (initialTab) setTab(initialTab); }, [initialTab]);
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  return (
    <div data-testid="war-room-hub" className="animate-enter">
      {/* Big marquee heading — distinct from the sub-tab strip */}
      <div className="mb-4 flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-orange-500/10 border border-orange-500/30 flex items-center justify-center">
          <Swords className="w-4 h-4 text-orange-400" />
        </div>
        <h1 className="text-white text-xl sm:text-2xl font-bold tracking-tight">AI War Room</h1>
        <span className="ml-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-300 border border-orange-500/30">v2</span>
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
