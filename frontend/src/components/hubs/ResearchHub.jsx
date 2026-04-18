import React, { useState } from 'react';
import { BookOpen, TrendingUp, Building2, Globe2, Radio, Wand2, Store, Database, FileText, Users } from 'lucide-react';
import AIHypothesis from '../AIHypothesis';
import MarketPrediction from '../MarketPrediction';
import CompanyResearch from '../CompanyResearch';
import MacroDashboard from '../MacroDashboard';
import useV2Nav from '../../hooks/useV2Nav';
import IconTabBar from './IconTabBar';

const MarketSignals = React.lazy(() => import('../MarketSignals'));
const StrategyBuilder = React.lazy(() => import('../StrategyBuilder'));
const StrategyMarketplace = React.lazy(() => import('../StrategyMarketplace'));
const MemoryDashboard = React.lazy(() => import('../MemoryDashboard'));
const StockFitFundamentals = React.lazy(() => import('../StockFitFundamentals'));
const StockFit13F = React.lazy(() => import('../StockFit13F'));
const StockDetailHub = React.lazy(() => import('./StockDetailHub'));

// v2 tab set — Hypothesis/Predictions/Signals moved to War Room, Company/StockFit/13F merged
const V2_TABS = [
  { key: 'stock',       label: 'Stock Detail', icon: Building2, desc: 'Per-ticker drill: overview, fundamentals, holders' },
  { key: 'macro',       label: 'Macro',        icon: Globe2,    desc: 'Global macro dashboard — rates, FX, commodities' },
  { key: 'strategy',    label: 'Strategy',     icon: Wand2,     desc: 'Build & backtest custom trading strategies' },
  { key: 'marketplace', label: 'Marketplace',  icon: Store,     desc: 'Browse shared strategies from the community' },
  { key: 'memory',      label: 'Memory',       icon: Database,  desc: 'AI prediction memory — episodic recall & lessons' },
];

const LEGACY_TABS = [
  { key: 'hypothesis',  label: 'Hypothesis',  icon: BookOpen,   desc: 'AI investment hypothesis — thesis generator' },
  { key: 'prediction',  label: 'Predictions', icon: TrendingUp, desc: 'Raw AI market predictions with confidence' },
  { key: 'company',     label: 'Company',     icon: Building2,  desc: 'Perplexity-style AI company research' },
  { key: 'stockfit',    label: 'StockFit',    icon: FileText,   desc: 'SEC EDGAR — Income, Balance Sheet, F-Score, Z-Score' },
  { key: '13f',         label: '13F Holders', icon: Users,      desc: 'Institutional positions & quarterly changes' },
  { key: 'macro',       label: 'Macro',       icon: Globe2,     desc: 'Global macro dashboard' },
  { key: 'signals',     label: 'Signals',     icon: Radio,      desc: 'Live technical & flow signals' },
  { key: 'strategy',    label: 'Strategy',    icon: Wand2,      desc: 'Strategy builder' },
  { key: 'marketplace', label: 'Marketplace', icon: Store,      desc: 'Strategy marketplace' },
  { key: 'memory',      label: 'Memory',      icon: Database,   desc: 'AI memory dashboard' },
];

export default function ResearchHub({ onSubscribe, onLogin, initialTab }) {
  const { enabled: v2Nav } = useV2Nav();
  const tabs = v2Nav ? V2_TABS : LEGACY_TABS;

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
      <IconTabBar
        tabs={tabs} value={tab} onChange={setTab}
        enabled={v2Nav}
        testIdPrefix="research-tab"
        legendTitle="Research Legend"
      />

      <React.Suspense fallback={fallback}>
        <div className="animate-enter">
          {tab === 'stock' && v2Nav && <StockDetailHub />}
          {tab === 'hypothesis' && !v2Nav && <AIHypothesis onSubscribe={onSubscribe} onLogin={onLogin} />}
          {tab === 'prediction' && !v2Nav && <MarketPrediction />}
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
