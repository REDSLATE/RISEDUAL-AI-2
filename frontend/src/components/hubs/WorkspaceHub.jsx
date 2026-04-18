import React, { useState } from 'react';
import { Briefcase, PieChart, BookOpen, LineChart, Users, Bot, Layers, Calculator, Radar as RadarIcon, AlertTriangle, Brain, Coins, HelpCircle, X } from 'lucide-react';
import useV2Nav from '../../hooks/useV2Nav';

const TABS = [
  { key: 'watchlist',   label: 'Watchlist',   icon: Briefcase,      desc: 'Your tracked symbols with AI scoring & sparklines' },
  { key: 'portfolio',   label: 'Portfolio',   icon: PieChart,       desc: 'Positions, allocation, risk concentration' },
  { key: 'journal',     label: 'Journal',     icon: BookOpen,       desc: 'Trade log with entry/exit notes & tagging' },
  { key: 'pnl',         label: 'P&L',         icon: LineChart,      desc: 'Realised & unrealised profit-and-loss tracker' },
  { key: 'paper',       label: 'Paper',       icon: LineChart,      desc: 'Paper trading — practise with simulated capital' },
  { key: 'bots',        label: 'Bots',        icon: Bot,            desc: 'Automated trading bots — deploy, monitor, stop' },
  { key: 'ml',          label: 'ML Controls', icon: Brain,          desc: 'Machine-learning pipeline: training & tier controls' },
  { key: 'orders',      label: 'Smart Orders',icon: Layers,         desc: 'Bracket, OCO, trailing-stop order builder' },
  { key: 'risk',        label: 'Risk Calc',   icon: Calculator,     desc: 'Position-size & stop-loss risk calculator' },
  { key: 'scanner',     label: 'Scanner',     icon: RadarIcon,      desc: 'Market scanner — RSI, MACD, breakout presets' },
  { key: 'credits',     label: 'Credits',     icon: Coins,          desc: 'AI credit balance, top-ups, usage history' },
  { key: 'referral',    label: 'Referrals',   icon: Users,          desc: 'Your referral link, rewards & leaderboard' },
  { key: 'failureloop', label: 'Loops',       icon: AlertTriangle,  desc: 'Toxic-spike & failure-loop detection dashboard' },
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

function LegendPopover({ onClose }) {
  // close on Escape
  React.useEffect(() => {
    const esc = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-[50]" onClick={onClose} />
      <div
        className="absolute right-0 top-full mt-2 z-[60] w-[min(420px,calc(100vw-24px))] rounded-xl border border-slate-700/60 bg-[#0b1426] shadow-2xl shadow-black/60 overflow-hidden animate-enter"
        data-testid="workspace-legend"
      >
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-700/50 bg-slate-900/60">
          <div className="flex items-center gap-2">
            <HelpCircle className="w-3.5 h-3.5 text-[#3DE8D9]" />
            <span className="text-white text-xs font-semibold tracking-wide uppercase">Workspace Legend</span>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white p-1 rounded" aria-label="Close legend">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto py-1">
          {TABS.map(t => {
            const Icon = t.icon;
            return (
              <div key={t.key} className="flex items-start gap-3 px-4 py-2 hover:bg-slate-800/60">
                <div className="w-7 h-7 rounded-md bg-slate-800 border border-slate-700/60 flex items-center justify-center shrink-0 mt-0.5">
                  <Icon className="w-3.5 h-3.5 text-[#3DE8D9]" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-white text-[12px] font-semibold leading-tight">{t.label}</div>
                  <div className="text-slate-400 text-[11px] leading-snug">{t.desc}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

export default function WorkspaceHub({ onSubscribe, initialTab }) {
  const { enabled: v2Nav } = useV2Nav();
  const [tab, setTab] = useState(initialTab || 'watchlist');
  const [legendOpen, setLegendOpen] = useState(false);
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;
  const active = TABS.find(t => t.key === tab) || TABS[0];

  return (
    <div data-testid="workspace-hub">
      {/* Tab bar */}
      <div className="relative mb-5 border-b border-slate-700/50">
        <div className="flex items-center gap-0.5 overflow-x-auto pb-1 pr-20 scrollbar-hide">
          {TABS.map(t => {
            const Icon = t.icon;
            const isActive = tab === t.key;
            if (v2Nav) {
              // Icon-only with native tooltip (translucent, browser-rendered)
              return (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  title={`${t.label} — ${t.desc}`}
                  aria-label={t.label}
                  className={`relative flex items-center justify-center w-10 h-10 rounded-t-lg shrink-0 transition-all ${
                    isActive
                      ? 'bg-slate-800 text-[#3DE8D9] border-b-2 border-[#3DE8D9]'
                      : 'text-slate-400 hover:text-white hover:bg-slate-800/40'
                  }`}
                  data-testid={`workspace-tab-${t.key}`}
                >
                  <Icon className="w-4 h-4" />
                </button>
              );
            }
            // Legacy (non-v2) appearance — icon + label
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`flex items-center gap-1 px-2 py-2 rounded-t-lg text-[11px] font-medium whitespace-nowrap shrink-0 transition-colors ${
                  isActive
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

        {/* Legend button — pinned to the right edge */}
        {v2Nav && (
          <button
            onClick={() => setLegendOpen(v => !v)}
            className={`absolute right-14 top-1/2 -translate-y-1/2 flex items-center justify-center w-8 h-8 rounded-lg transition-colors ${
              legendOpen ? 'bg-[#3DE8D9]/15 text-[#3DE8D9]' : 'text-slate-500 hover:text-[#3DE8D9] hover:bg-slate-800/60'
            }`}
            title="Icon legend"
            aria-label="Open icon legend"
            data-testid="workspace-legend-btn"
          >
            <HelpCircle className="w-4 h-4" />
          </button>
        )}

        {legendOpen && <LegendPopover onClose={() => setLegendOpen(false)} />}
      </div>

      {/* Active tab label — small breadcrumb under icons in v2 mode */}
      {v2Nav && (
        <div className="-mt-3 mb-4 flex items-center gap-2">
          <active.icon className="w-3.5 h-3.5 text-[#3DE8D9]" />
          <span className="text-white text-sm font-semibold">{active.label}</span>
          <span className="text-slate-500 text-[11px] hidden sm:inline">· {active.desc}</span>
        </div>
      )}

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
