/**
 * DashboardView — the default `activeView === 'dashboard'` block.
 *
 * Extracted from `App.js::AppContent` (code-review finding: 350-line
 * component, complexity 37). Pure presentational: every piece of state
 * is already owned elsewhere (auth, modals, v2Nav hook, active view
 * router). This keeps App.js readable as a router / modal glue layer
 * and isolates the dashboard composition for future iteration.
 *
 * Props map 1:1 to what AppContent used to have in-scope, nothing more.
 */
import React from 'react';
import Watchlist from './Watchlist';
import AIWarRoom from './AIWarRoom';
import MarketsSection from './MarketsSection';
import FearGreedGauge from './FearGreedGauge';
import LiveInsightsFeed from './LiveInsightsFeed';
import OrderFlowPanel from './OrderFlowPanel';
import WhaleRadar from './WhaleRadar';
import AIIntelligence from './AIIntelligence';
import AdditionalSections from './AdditionalSections';

// NB: Tailwind's JIT compiler can't detect dynamic class strings like
// `bg-${accent}-500/10`, so we build a fully-resolved class map per
// accent color. Add a new entry here to introduce a new hub card tint.
const ACCENT_CLASSES = {
  cyan: {
    border: 'hover:border-[#3DE8D9]/30',
    iconBg: 'bg-[#3DE8D9]/10',
    iconText: 'text-[#3DE8D9]',
  },
  violet: {
    border: 'hover:border-violet-400/30',
    iconBg: 'bg-violet-500/10',
    iconText: 'text-violet-400',
  },
  amber: {
    border: 'hover:border-amber-400/30',
    iconBg: 'bg-amber-500/10',
    iconText: 'text-amber-400',
  },
};

const HubCard = ({ onClick, letter, title, subtitle, accent, testId }) => {
  const cls = ACCENT_CLASSES[accent] || ACCENT_CLASSES.cyan;
  return (
    <button
      onClick={onClick}
      className={`group flex items-center gap-3 p-3 rounded-xl bg-slate-800/40 border border-slate-700/40 ${cls.border} hover:bg-slate-800/70 transition-all text-left`}
      data-testid={testId}
    >
      <div className={`w-9 h-9 rounded-lg ${cls.iconBg} flex items-center justify-center shrink-0`}>
        <span className={`${cls.iconText} text-sm font-bold`}>{letter}</span>
      </div>
      <div className="min-w-0">
        <div className="text-white text-xs font-semibold">{title}</div>
        <p className="text-slate-500 text-[10px] truncate">{subtitle}</p>
      </div>
    </button>
  );
};

const DashboardView = ({ onSubscribe, onLogin, navigateTo, v2Nav }) => (
  <>
    {/* Compact Watchlist */}
    <div className="mb-6 sm:mb-8 animate-enter" data-testid="watchlist-section">
      <Watchlist onSubscribe={onSubscribe} />
    </div>

    {/* AI War Room — inline classic block, hidden when v2 consolidated War
        Room replaces it. */}
    {!v2Nav && (
      <div id="ai-war-room" className="mb-6 sm:mb-8 animate-enter">
        <AIWarRoom onSubscribe={onSubscribe} onLogin={onLogin} />
      </div>
    )}
    {v2Nav && (
      <div className="mb-6 sm:mb-8 animate-enter">
        <button
          onClick={() => navigateTo('warroom')}
          className="w-full group rounded-2xl border border-orange-500/30 bg-gradient-to-br from-orange-950/40 to-slate-900/40 p-4 sm:p-5 hover:border-orange-400/60 hover:from-orange-950/60 transition-all text-left"
          data-testid="dashboard-warroom-card"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-orange-500/15 border border-orange-500/30 flex items-center justify-center shrink-0">
              <span className="text-orange-400 text-lg font-bold">⚔</span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <h3 className="text-white font-bold text-sm sm:text-base">AI War Room</h3>
                <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-300">Open</span>
              </div>
              <p className="text-slate-400 text-xs">
                Adversarial AI · Predictions · Hypothesis · Signals · Intelligence — all in one command center.
              </p>
            </div>
            <span className="text-orange-400 text-xl group-hover:translate-x-1 transition-transform">→</span>
          </div>
        </button>
      </div>
    )}

    {/* Markets + sentiment */}
    <div id="sector-heatmap" className="mb-6 sm:mb-8 animate-enter">
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-3"><MarketsSection /></div>
        <div className="lg:col-span-1"><FearGreedGauge /></div>
      </div>
    </div>

    {/* Key Signals */}
    <div id="live-insights" className="mb-6 sm:mb-8 animate-enter"><LiveInsightsFeed /></div>
    <div id="order-flow" className="mb-6 sm:mb-8 animate-enter"><OrderFlowPanel /></div>
    <div id="whale-radar" className="mb-6 sm:mb-8 animate-enter"><WhaleRadar /></div>

    {/* AI Intelligence — hidden when v2 (lives inside War Room). */}
    {!v2Nav && (
      <div id="ai-intelligence" className="mb-6 sm:mb-8 animate-enter relative z-10">
        <AIIntelligence onSubscribe={onSubscribe} />
      </div>
    )}

    {/* Hub nav row */}
    <div
      className="rounded-2xl border border-slate-700/50 bg-slate-800/20 p-4 mb-6 animate-enter"
      data-testid="explore-hubs"
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white">Explore</h3>
        <span className="text-[10px] text-slate-500">Jump to a destination</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <HubCard
          onClick={() => navigateTo('research')}
          letter="R"
          title="Research"
          subtitle="AI Hypothesis, Predictions, Macro"
          accent="cyan"
          testId="nav-to-research"
        />
        <HubCard
          onClick={() => navigateTo('options')}
          letter="O"
          title="Options"
          subtitle="Radar, Flow, Dark Pool"
          accent="violet"
          testId="nav-to-options"
        />
        <HubCard
          onClick={() => navigateTo('workspace')}
          letter="W"
          title="Workspace"
          subtitle="Portfolio, Journal, Bots, Tools"
          accent="amber"
          testId="nav-to-workspace"
        />
      </div>
    </div>

    <AdditionalSections />
  </>
);

export default DashboardView;
