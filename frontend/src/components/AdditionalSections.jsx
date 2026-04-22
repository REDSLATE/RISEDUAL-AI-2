import React, { useState } from 'react';
import { Zap, Rocket, BarChart3 } from 'lucide-react';
import DataTable from './DataTable';
import { momentumData, fastMoverCallsData, unusualVolumeData } from '../mockData';

const TABS = [
  { key: 'momentum', label: 'Momentum', icon: Zap },
  { key: 'fast-movers', label: 'Fast Movers', icon: Rocket },
  { key: 'unusual-vol', label: 'Unusual Volume', icon: BarChart3 },
];

const momentumColumns = [
  { key: 'contract', label: 'Contract', sortable: true },
  { key: 'price', label: 'Price', sortable: true },
  { key: 'returns', label: '% Returns', sortable: true },
  { key: 'power', label: 'Power', sortable: true },
  { key: 'volOI', label: 'Vol/OI', sortable: true },
  { key: 'ivRank', label: 'IV Rank', sortable: true },
  { key: 'aiScore', label: 'AI Score', sortable: true },
];

const unusualVolumeColumns = [
  { key: 'contract', label: 'Contract', sortable: true },
  { key: 'price', label: 'Price', sortable: true },
  { key: 'volOI', label: 'Vol/OI', sortable: true },
  { key: 'returns', label: '% Returns', sortable: true },
  { key: 'power', label: 'Power', sortable: true },
  { key: 'sentiment', label: 'Sentiment', sortable: true },
  { key: 'aiScore', label: 'AI Score', sortable: true },
];

const AdditionalSections = () => {
  const [tab, setTab] = useState('momentum');

  return (
    <div className="mt-8 mb-6" data-testid="strategy-signals">
      <div className="rounded-2xl border border-slate-700/50 bg-slate-800/20 overflow-hidden">
        {/* Tab bar */}
        <div className="flex items-center gap-1 px-3 pt-3 pb-0 overflow-x-auto border-b border-slate-700/40">
          {TABS.map(t => {
            const Icon = t.icon;
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`flex items-center gap-1.5 px-3 py-2 rounded-t-lg text-xs font-medium whitespace-nowrap transition-colors ${
                  tab === t.key
                    ? 'bg-slate-800 text-[#3DE8D9] border-b-2 border-[#3DE8D9]'
                    : 'text-slate-400 hover:text-white'
                }`}
                data-testid={`strategy-tab-${t.key}`}
              >
                <Icon className="w-3.5 h-3.5" />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="p-0">
          {tab === 'momentum' && (
            <div id="momentum">
              <DataTable title="Momentum Close Strength" columns={momentumColumns} data={momentumData} tradeVariant="options" />
            </div>
          )}
          {tab === 'fast-movers' && (
            <div id="fast-movers">
              <DataTable title="Fast Mover Calls" columns={momentumColumns} data={fastMoverCallsData} tradeVariant="options" />
            </div>
          )}
          {tab === 'unusual-vol' && (
            <div id="unusual-volume">
              <DataTable title="Unusual Options Volume" columns={unusualVolumeColumns} data={unusualVolumeData} tradeVariant="options" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default AdditionalSections;
