import React, { useState } from 'react';
import { Briefcase, PieChart, BookOpen, LineChart, Users, Bot, Layers, Calculator, Radar as RadarIcon, AlertTriangle, Brain, Coins } from 'lucide-react';

const TABS = [
  { key: 'watchlist', label: 'Watchlist', icon: Briefcase },
  { key: 'portfolio', label: 'Portfolio', icon: PieChart },
  { key: 'journal', label: 'Journal', icon: BookOpen },
  { key: 'pnl', label: 'P&L', icon: LineChart },
  { key: 'paper', label: 'Paper', icon: LineChart },
  { key: 'bots', label: 'Bots', icon: Bot },
  { key: 'ml', label: 'ML Controls', icon: Brain },
  { key: 'orders', label: 'Orders', icon: Layers },
  { key: 'risk', label: 'Risk', icon: Calculator },
  { key: 'scanner', label: 'Scanner', icon: RadarIcon },
  { key: 'credits', label: 'Credits', icon: Coins },
  { key: 'referral', label: 'Referrals', icon: Users },
  { key: 'failureloop', label: 'Loops', icon: AlertTriangle },
];

// Lazy-load all components
const Watchlist = React.lazy(() => import('../Watchlist'));
const WatchlistIntelligence = React.lazy(() => import('../WatchlistIntelligence'));
const PortfolioAnalyzer = React.lazy(() => import('../PortfolioAnalyzer'));
const TradingJournal = React.lazy(() => import('../TradingJournal'));
const PnLTracker = React.lazy(() => import('../PnLTracker'));
const PaperTrading = React.lazy(() => import('../PaperTrading'));
const TradingBotPanel = React.lazy(() => import('../TradingBotPanel'));
const SmartOrderPanel = React.lazy(() => import('../SmartOrderPanel'));
const RiskCalculator = React.lazy(() => import('../RiskCalculator'));
const MarketScanner = React.lazy(() => import('../MarketScanner'));
const MLControls = React.lazy(() => import('../MLControls'));
const CreditStore = React.lazy(() => import('../CreditStore'));
const ReferralLeaderboard = React.lazy(() => import('../ReferralLeaderboard'));
const FailureLoopDashboard = React.lazy(() => import('../FailureLoopDashboard'));

export default function WorkspaceHub({ onSubscribe, initialTab }) {
  const [tab, setTab] = useState(initialTab || 'watchlist');
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  return (
    <div data-testid="workspace-hub">
      <div className="flex items-center gap-1 overflow-x-auto pb-1 mb-5 border-b border-slate-700/50 scrollbar-hide">
        {TABS.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1 px-2 py-2 rounded-t-lg text-[11px] font-medium whitespace-nowrap shrink-0 transition-colors ${
                tab === t.key
                  ? 'bg-slate-800 text-[#3DE8D9] border-b-2 border-[#3DE8D9]'
                  : 'text-slate-400 hover:text-white'
              }`}
              data-testid={`workspace-tab-${t.key}`}
            >
              <Icon className="w-3 h-3" />
              {t.label}
            </button>
          );
        })}
      </div>

      <React.Suspense fallback={fallback}>
        <div className="animate-enter">
          {tab === 'watchlist' && (
            <div className="space-y-6">
              <Watchlist onSubscribe={onSubscribe} />
              <WatchlistIntelligence onSubscribe={onSubscribe} />
            </div>
          )}
          {tab === 'portfolio' && <PortfolioAnalyzer onSubscribe={onSubscribe} />}
          {tab === 'journal' && <TradingJournal onSubscribe={onSubscribe} />}
          {tab === 'pnl' && <PnLTracker />}
          {tab === 'paper' && <PaperTrading />}
          {tab === 'bots' && <TradingBotPanel />}
          {tab === 'ml' && <MLControls />}
          {tab === 'orders' && <SmartOrderPanel />}
          {tab === 'risk' && <RiskCalculator onApplyToSmartOrder={() => setTab('orders')} />}
          {tab === 'scanner' && <MarketScanner />}
          {tab === 'credits' && <CreditStore />}
          {tab === 'referral' && <ReferralLeaderboard />}
          {tab === 'failureloop' && <FailureLoopDashboard />}
        </div>
      </React.Suspense>
    </div>
  );
}
