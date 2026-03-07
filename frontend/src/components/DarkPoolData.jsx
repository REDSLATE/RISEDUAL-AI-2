import React, { useState, useEffect } from 'react';
import { Eye, TrendingUp, Activity } from 'lucide-react';
import DataTable from './DataTable';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const DarkPoolData = () => {
  const [darkPoolData, setDarkPoolData] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDarkPoolData();
    const interval = setInterval(fetchDarkPoolData, 120000); // Update every 2 minutes
    return () => clearInterval(interval);
  }, []);

  const fetchDarkPoolData = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/dark-pool`);
      if (response.ok) {
        const data = await response.json();
        setDarkPoolData(data);
      }
    } catch (error) {
      console.error('Error fetching dark pool data:', error);
    } finally {
      setLoading(false);
    }
  };

  const columns = [
    { key: 'symbol', label: 'Symbol', sortable: true },
    { key: 'darkPoolVolume', label: 'Dark Pool Vol', sortable: true },
    { key: 'totalVolume', label: 'Total Volume', sortable: true },
    { key: 'darkPoolPercent', label: 'Dark Pool %', sortable: true },
    { key: 'averagePrice', label: 'Avg Price', sortable: true },
    { key: 'priceChange', label: 'Price Change', sortable: true },
    { key: 'sentiment', label: 'Sentiment', sortable: true },
  ];

  // Format data for DataTable
  const formattedData = darkPoolData.map(item => ({
    symbol: item.symbol,
    darkPoolVolume: item.darkPoolVolume.toLocaleString(),
    totalVolume: item.totalVolume.toLocaleString(),
    darkPoolPercent: `${item.darkPoolPercent}%`,
    averagePrice: `$${item.averagePrice}`,
    priceChange: item.priceChange >= 0 ? `+${item.priceChange}%` : `${item.priceChange}%`,
    sentiment: item.sentiment,
  }));

  if (loading) {
    return (
      <div className="bg-[#0a0a0b] rounded-lg border border-gray-800 p-6">
        <div className="text-gray-400">Loading dark pool data...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6 mt-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-purple-600 rounded-lg flex items-center justify-center">
          <Eye className="w-6 h-6 text-white" />
        </div>
        <div>
          <h2 className="text-white text-2xl font-bold">Dark Pool Trading</h2>
          <p className="text-gray-400 text-sm">Off-exchange institutional trading activity</p>
        </div>
      </div>

      {/* Dark Pool Table */}
      <DataTable
        title="Dark Pool Activity"
        subtitle="Real-time dark pool trading data showing institutional buying and selling patterns"
        columns={columns}
        data={formattedData}
      />

      {/* Info Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-[#0a0a0b] border border-gray-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Activity className="w-5 h-5 text-purple-400" />
            <h3 className="text-white font-semibold">What is Dark Pool?</h3>
          </div>
          <p className="text-gray-400 text-sm">
            Dark pools are private exchanges where institutional investors trade large blocks of securities anonymously.
          </p>
        </div>
        
        <div className="bg-[#0a0a0b] border border-gray-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-5 h-5 text-green-400" />
            <h3 className="text-white font-semibold">Why It Matters</h3>
          </div>
          <p className="text-gray-400 text-sm">
            High dark pool activity can indicate institutional interest and potential price movements in securities.
          </p>
        </div>
        
        <div className="bg-[#0a0a0b] border border-gray-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Eye className="w-5 h-5 text-blue-400" />
            <h3 className="text-white font-semibold">How to Use</h3>
          </div>
          <p className="text-gray-400 text-sm">
            Monitor dark pool percentage - higher values suggest significant institutional positioning.
          </p>
        </div>
      </div>
    </div>
  );
};

export default DarkPoolData;