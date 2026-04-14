import React, { useState } from 'react';
import { Briefcase, PieChart, BookOpen, LineChart, Users, Bot, Layers, Calculator, Radar as RadarIcon, AlertTriangle } from 'lucide-react';

const TABS = [
  { key: 'watchlist', label: 'Watchlist', icon: Briefcase },
  { key: 'portfolio', label: 'Portfolio', icon: PieChart },
  { key: 'journal', label: 'Journal', icon: BookOpen },
  { key: 'pnl', label: 'P&L', icon: LineChart },
  { key: 'paper', label: 'Paper', icon: LineChart },
  { key: 'bots', label: 'Bots', icon: Bot },
  { key: 'orders', label: 'Orders', icon: Layers },
  { key: 'risk', label: 'Risk', icon: Calculator },
  { key: 'scanner', label: 'Scanner', icon: RadarIcon },
  { key: 'referral', label: 'Referrals', icon: Users },
  { key: 'failureloop', label: 'Loops', icon: AlertTriangle },
];

export default function WorkspaceHub({
  onSubscribe, initialTab,
  onOpenPortfolio, onOpenJournal, onOpenPaperTrading, onOpenBots,
  onOpenSmartOrders, onOpenRiskCalc, onOpenScanner, onOpenFailureLoop,
}) {
  const [tab, setTab] = useState(initialTab || 'watchlist');

  // Lazy-load heavy components
  const Watchlist = React.lazy(() => import('../Watchlist'));
  const WatchlistIntelligence = React.lazy(() => import('../WatchlistIntelligence'));
  const PnLTracker = React.lazy(() => import('../PnLTracker'));
  const ReferralLeaderboard = React.lazy(() => import('../ReferralLeaderboard'));
  const BotsDashboard = React.lazy(() => import('../BotsDashboard'));
  const FailureLoopDashboard = React.lazy(() => import('../FailureLoopDashboard'));

  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  // Some tools open as modals — trigger them and show a message
  const modalTabs = {
    portfolio: { fn: onOpenPortfolio, label: 'Portfolio Analyzer' },
    journal: { fn: onOpenJournal, label: 'Trading Journal' },
    paper: { fn: onOpenPaperTrading, label: 'Paper Trading' },
    orders: { fn: onOpenSmartOrders, label: 'Smart Orders' },
    risk: { fn: onOpenRiskCalc, label: 'Risk Calculator' },
    scanner: { fn: onOpenScanner, label: 'Market Scanner' },
  };

  const handleTabClick = (key) => {
    if (modalTabs[key]) {
      modalTabs[key].fn?.();
    } else {
      setTab(key);
    }
  };

  return (
    <div data-testid="workspace-hub">
      <div className="flex items-center gap-1 overflow-x-auto pb-1 mb-5 border-b border-slate-700/50 scrollbar-hide">
        {TABS.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => handleTabClick(t.key)}
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
          {tab === 'pnl' && <PnLTracker />}
          {tab === 'bots' && <BotsDashboard onOpenBots={onOpenBots} />}
          {tab === 'referral' && <ReferralLeaderboard />}
          {tab === 'failureloop' && <FailureLoopDashboard onClose={() => setTab('watchlist')} />}
        </div>
      </React.Suspense>
    </div>
  );
}
