import React from 'react';
import DataTable from './DataTable';
import { optionsFlowData } from '../mockData';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';

const OptionsFlowScreener = () => {
  const columns = [
    { key: 'contract', label: 'Contract', sortable: true },
    { key: 'price', label: 'Price', sortable: true },
    { key: 'returns', label: '% Returns', sortable: true },
    { key: 'volOI', label: 'Vol/OI', sortable: true },
    { key: 'power', label: 'Power', sortable: true },
    { key: 'ivRank', label: 'IV Rank', sortable: true },
    { key: 'aiScore', label: 'AI Score', sortable: true },
  ];

  return (
    <div className="space-y-6 mt-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-[#0052FF] rounded-xl flex items-center justify-center">
          <svg viewBox="0 0 24 24" fill="none" className="w-6 h-6">
            <circle cx="12" cy="12" r="10" stroke="white" strokeWidth="2" />
            <path d="M12 6v6l4 2" stroke="white" strokeWidth="2" />
          </svg>
        </div>
        <div>
          <h2 className="text-white text-2xl font-bold">Options Flow Screener</h2>
          <p className="text-slate-400 text-sm">OPRA data is delayed by 15 minutes</p>
        </div>
      </div>

      {/* Content */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Most Actively Traded */}
        <DataTable
          title="Most Actively Traded"
          columns={columns}
          data={optionsFlowData.mostActivelyTraded}
        />

        {/* 0-DTE Edge */}
        <DataTable
          title="0-DTE Edge"
          columns={columns}
          data={optionsFlowData.dteEdge}
        />
      </div>

      {/* Volatility Opportunities */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <DataTable
          title="Volatility Opportunities (Low IV Rank)"
          columns={columns}
          data={optionsFlowData.volatilityLow}
        />
        <DataTable
          title="Volatility Opportunities (High IV Rank)"
          columns={columns}
          data={optionsFlowData.volatilityHigh}
        />
      </div>
    </div>
  );
};

export default OptionsFlowScreener;