import React, { useState } from 'react';
import { BookOpen, TrendingUp, Building2, Globe2, Radio, Wand2, Store, Database, FileText, Users } from 'lucide-react';
import AIHypothesis from '../AIHypothesis';
import MarketPrediction from '../MarketPrediction';
import CompanyResearch from '../CompanyResearch';
import MacroDashboard from '../MacroDashboard';
import useV2Nav from '../../hooks/useV2Nav';

const MarketSignals = React.lazy(() => import('../MarketSignals'));
const StrategyBuilder = React.lazy(() => import('../StrategyBuilder'));
const StrategyMarketplace = React.lazy(() => import('../StrategyMarketplace'));
const MemoryDashboard = React.lazy(() => import('../MemoryDashboard'));
const StockFitFundamentals = React.lazy(() => import('../StockFitFundamentals'));
const StockFit13F = React.lazy(() => import('../StockFit13F'));
const StockDetailHub = React.lazy(() => import('./StockDetailHub'));

const TABS = [
  { key: 'hypothesis', label: 'Hypothesis', icon: BookOpen, v2: false },
  { key: 'prediction', label: 'Predictions', icon: TrendingUp, v2: false },
  // v2: Company + StockFit + 13F collapse into one "Stock Detail" tab
  { key: 'stock',      label: 'Stock Detail', icon: Building2, onlyV2: true },
  { key: 'company', label: 'Company', icon: Building2, v2: false },
  { key: 'stockfit', label: 'StockFit', icon: FileText, v2: false },
  { key: '13f', label: '13F Holders', icon: Users, v2: false },
  { key: 'macro', label: 'Macro', icon: Globe2 },
  { key: 'signals', label: 'Signals', icon: Radio, v2: false },
  { key: 'strategy', label: 'Strategy', icon: Wand2 },
  { key: 'marketplace', label: 'Marketplace', icon: Store },
  { key: 'memory', label: 'Memory', icon: Database },
];

export default function ResearchHub({ onSubscribe, onLogin, initialTab }) {
  const { enabled: v2Nav } = useV2Nav();
  // When v2 is on, Hypothesis/Predictions/Signals live in the War Room hub.
  // Company/StockFit/13F collapse into a single "Stock Detail" tab.
  const visibleTabs = v2Nav
    ? TABS.filter(t => t.v2 !== false)
    : TABS.filter(t => !t.onlyV2);

  // Map legacy initialTab values to the v2-merged equivalents
  const mapInitialTab = (raw) => {
    if (!raw) return v2Nav ? 'stock' : 'hypothesis';
    if (v2Nav) {
      if (['hypothesis', 'prediction', 'signals'].includes(raw)) return 'stock';
      if (['company', 'stockfit', '13f'].includes(raw)) return 'stock';
    }
    return raw;
  };
  const [tab, setTab] = useState(mapInitialTab(initialTab));
  const fallback = <div className="text-slate-400 text-sm py-8 text-center">Loading...</div>;

  return (
    <div data-testid="research-hub">
      <div className="flex items-center gap-1 overflow-x-auto pb-1 mb-5 border-b border-slate-700/50 scrollbar-hide">
        {visibleTabs.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-t-lg text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                tab === t.key
                  ? 'bg-slate-800 text-[#3DE8D9] border-b-2 border-[#3DE8D9]'
                  : 'text-slate-400 hover:text-white'
              }`}
              data-testid={`research-tab-${t.key}`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      <React.Suspense fallback={fallback}>
        <div className="animate-enter">
          {tab === 'hypothesis' && !v2Nav && <AIHypothesis onSubscribe={onSubscribe} onLogin={onLogin} />}
          {tab === 'prediction' && !v2Nav && <MarketPrediction />}
          {tab === 'stock' && v2Nav && <StockDetailHub />}
          {tab === 'company' && !v2Nav && <CompanyResearch />}
          {tab === 'stockfit' && !v2Nav && <StockFitFundamentals />}
          {tab === '13f' && !v2Nav && <StockFit13F />}
          {tab === 'macro' && <MacroDashboard onSubscribe={onSubscribe} />}
          {tab === 'signals' && !v2Nav && <MarketSignals onSubscribe={onSubscribe} />}
          {tab === 'strategy' && <StrategyBuilder onSubscribe={onSubscribe} />}
          {tab === 'marketplace' && <StrategyMarketplace />}
          {tab === 'memory' && <MemoryDashboard onSubscribe={onSubscribe} />}
        </div>
      </React.Suspense>
    </div>
  );
}
