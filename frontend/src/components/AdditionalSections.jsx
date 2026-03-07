import React from 'react';
import DataTable from './DataTable';
import { momentumData, fastMoverCallsData, unusualVolumeData } from '../mockData';

const AdditionalSections = () => {
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

  return (
    <div className="space-y-6 mt-8">
      {/* Momentum Close Strength and Fast Mover Calls */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div id="momentum">
          <DataTable
            title="Momentum Close Strength"
            columns={momentumColumns}
            data={momentumData}
          />
        </div>
        <div id="fast-movers">
          <DataTable
            title="Fast Mover Calls"
            columns={momentumColumns}
            data={fastMoverCallsData}
          />
        </div>
      </div>

      {/* Unusual Options Volume */}
      <div id="unusual-volume">
        <DataTable
          title="Unusual Options Volume"
          columns={unusualVolumeColumns}
          data={unusualVolumeData}
        />
      </div>
    </div>
  );
};

export default AdditionalSections;