import React, { useState } from 'react';
import { Swords, TrendingUp, BookOpen, Radio, Sparkles } from 'lucide-react';
import AIWarRoom from '../AIWarRoom';
import AIHypothesis from '../AIHypothesis';
import MarketPrediction from '../MarketPrediction';
import AIIntelligence from '../AIIntelligence';

const MarketSignals = React.lazy(() => import('../MarketSignals'));

const TABS = [
  { key: 'adversarial', label: 'Adversarial AI', icon: Swords, desc: 'Strategist vs. Auditor adversarial loop' },
  { key: 'prediction',  label: 'Predictions',   icon: TrendingUp, desc: 'Raw AI market verdicts & confidence' },
  { key: 'hypothesis',  label: 'Hypothesis',    icon: BookOpen,   desc: 'Thesis generator — reasoning before the call' },
  { key: 'signals',     label: 'Signals',       icon: Radio,      desc: 'Live technical & flow-based signals' },
  { key: 'intelligence',label: 'Intelligence',  icon: Sparkles,   desc: 'Multi-model AI consensus & memory recall' },
];

/**
 * WarRoomHub — consolidated AI command center.
 *
 * Merges what used to be spread across:
 *   - Dashboard > AI War Room        → Adversarial AI
 *   - Research  > Predictions        → Predictions
 *   - Research  > Hypothesis         → Hypothesis
 *   - Research  > Signals            → Signals
 *   - Dashboard > AI Intelligence    → Intelligence
 */
export default function WarRoomHub({ onSubscribe, onLogin, initialTab }) {
  const [tab, setTab] = useState(initialTab || 'adversarial');
  const active = TABS.find(t => t.key === tab) || TABS[0];
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  return (
    <div data-testid="war-room-hub" className="animate-enter">
      {/* Header */}
      <div className="mb-5">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-7 h-7 rounded-lg bg-orange-500/10 border border-orange-500/30 flex items-center justify-center">
            <Swords className="w-4 h-4 text-orange-400" />
          </div>
          <h1 className="text-white text-xl sm:text-2xl font-bold tracking-tight">AI War Room</h1>
          <span className="ml-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-300 border border-orange-500/30">v2</span>
        </div>
        <p className="text-slate-400 text-xs sm:text-sm">
          {active.desc}
        </p>
      </div>

      {/* Sub-nav tabs */}
      <div className="flex items-center gap-1 overflow-x-auto pb-1 mb-5 border-b border-slate-700/50 scrollbar-hide">
        {TABS.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-t-lg text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                tab === t.key
                  ? 'bg-slate-800 text-orange-300 border-b-2 border-orange-400'
                  : 'text-slate-400 hover:text-white'
              }`}
              data-testid={`warroom-tab-${t.key}`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Active surface */}
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
