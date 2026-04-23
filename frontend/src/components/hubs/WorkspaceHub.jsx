import React, { useState } from 'react';
import { Briefcase, PieChart, BookOpen, LineChart, Users, Bot, Layers, Calculator, Radar as RadarIcon, AlertTriangle, Brain, Coins, Activity } from 'lucide-react';
import useV2Nav from '../../hooks/useV2Nav';
import IconTabBar from './IconTabBar';

const TABS = [
  { key: 'agent',       label: 'Agent',        icon: Activity,       desc: 'Live narrative of what the AI agent is doing right now' },
  { key: 'watchlist',   label: 'Watchlist',    icon: Briefcase,      desc: 'Your tracked symbols with AI scoring & sparklines' },
  { key: 'portfolio',   label: 'Portfolio',    icon: PieChart,       desc: 'Positions, allocation, risk concentration' },
  { key: 'journal',     label: 'Journal',      icon: BookOpen,       desc: 'Trade log with entry/exit notes & tagging' },
  { key: 'pnl',         label: 'P&L',          icon: LineChart,      desc: 'Realised & unrealised profit-and-loss tracker' },
  { key: 'paper',       label: 'Paper',        icon: LineChart,      desc: 'Paper trading — practise with simulated capital' },
  { key: 'bots',        label: 'Bots',         icon: Bot,            desc: 'Automated trading bots — deploy, monitor, stop' },
  { key: 'ml',          label: 'ML Controls',  icon: Brain,          desc: 'Machine-learning pipeline: training & tier controls' },
  { key: 'orders',      label: 'Smart Orders', icon: Layers,         desc: 'Bracket, OCO, trailing-stop order builder' },
  { key: 'risk',        label: 'Risk Calc',    icon: Calculator,     desc: 'Position-size & stop-loss risk calculator' },
  { key: 'scanner',     label: 'Scanner',      icon: RadarIcon,      desc: 'Market scanner — RSI, MACD, breakout presets' },
  { key: 'credits',     label: 'Credits',      icon: Coins,          desc: 'AI credit balance, top-ups, usage history' },
  { key: 'referral',    label: 'Referrals',    icon: Users,          desc: 'Your referral link, rewards & leaderboard' },
  { key: 'failureloop', label: 'Loops',        icon: AlertTriangle,  desc: 'Toxic-spike & failure-loop detection dashboard' },
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
const AgentActivityFeed = React.lazy(() => import('../AgentActivityFeed'));
const FeatureStabilityPanel = React.lazy(() => import('../FeatureStabilityPanel'));

export default function WorkspaceHub({ onSubscribe, initialTab }) {
  const { enabled: v2Nav } = useV2Nav();
  const [tab, setTab] = useState(initialTab || 'agent');
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  return (
    <div data-testid="workspace-hub">
      <IconTabBar
        tabs={TABS} value={tab} onChange={setTab}
        enabled={v2Nav}
        testIdPrefix="workspace-tab"
        legendTitle="Workspace Legend"
      />
      <React.Suspense fallback={fallback}>
        <div className="animate-enter">
          {tab === 'agent' && (
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-4">
              <AgentActivityFeed />
              <FeatureStabilityPanel />
            </div>
          )}
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
