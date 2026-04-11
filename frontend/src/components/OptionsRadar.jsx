import React from 'react';
import DataTable from './DataTable';
import { optionsRadarData } from '../mockData';

const OptionsRadar = () => {
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
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-[#3DE8D9] rounded-xl flex items-center justify-center">
          <svg viewBox="0 0 24 24" fill="none" className="w-6 h-6">
            <circle cx="12" cy="12" r="8" stroke="white" strokeWidth="2" />
            <circle cx="12" cy="12" r="3" fill="white" />
          </svg>
        </div>
        <div>
          <h2 className="text-white text-lg sm:text-2xl font-bold">AI Options Radar</h2>
          <p className="text-slate-300 text-sm">OPRA data is delayed by 15 minutes</p>
        </div>
      </div>

      {/* Most Actively Traded */}
      <DataTable
        title="Most Actively Traded"
        columns={columns}
        data={optionsRadarData.mostActivelyTraded}
        showFilters={true}
      />

      {/* Volatility Opportunities */}
      <div className="grid grid-cols-1 gap-6">
        <DataTable
          title="Volatility Opportunities (Low IV Rank)"
          columns={columns}
          data={optionsRadarData.volatilityOpportunities}
          showFilters={true}
        />
      </div>
    </div>
  );
};

export default OptionsRadar;