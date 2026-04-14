import React, { useState } from 'react';
import { BookOpen, TrendingUp, Building2, Globe2 } from 'lucide-react';
import AIHypothesis from '../AIHypothesis';
import MarketPrediction from '../MarketPrediction';
import CompanyResearch from '../CompanyResearch';
import MacroDashboard from '../MacroDashboard';

const TABS = [
  { key: 'hypothesis', label: 'Hypothesis', icon: BookOpen },
  { key: 'prediction', label: 'Predictions', icon: TrendingUp },
  { key: 'company', label: 'Company', icon: Building2 },
  { key: 'macro', label: 'Macro', icon: Globe2 },
];

export default function ResearchHub({ onSubscribe, onLogin, initialTab }) {
  const [tab, setTab] = useState(initialTab || 'hypothesis');

  return (
    <div data-testid="research-hub">
      <div className="flex items-center gap-1 overflow-x-auto pb-1 mb-5 border-b border-slate-700/50 scrollbar-hide">
        {TABS.map(t => {
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

      <div className="animate-enter">
        {tab === 'hypothesis' && <AIHypothesis onSubscribe={onSubscribe} onLogin={onLogin} />}
        {tab === 'prediction' && <MarketPrediction />}
        {tab === 'company' && <CompanyResearch />}
        {tab === 'macro' && <MacroDashboard onSubscribe={onSubscribe} />}
      </div>
    </div>
  );
}
