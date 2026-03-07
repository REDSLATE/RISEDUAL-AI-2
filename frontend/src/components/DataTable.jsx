import React, { useState } from 'react';
import { ThumbsUp, ThumbsDown, HelpCircle, Download } from 'lucide-react';
import { Button } from './ui/button';
import FilterPanel from './FilterPanel';

const DataTable = ({ title, subtitle, columns, data, showLikes = true, showFilters = false }) => {
  const [filteredData, setFilteredData] = useState(data);
  const [filters, setFilters] = useState(null);

  const getPowerColor = (power) => {
    if (power >= 80) return 'bg-green-500';
    if (power >= 60) return 'bg-yellow-500';
    return 'bg-orange-500';
  };

  const getPowerBarWidth = (power) => {
    return `${power}%`;
  };

  const exportToCSV = () => {
    const csvData = filteredData.map(row => {
      return columns.map(col => row[col.key]).join(',');
    });
    const headers = columns.map(col => col.label).join(',');
    const csv = [headers, ...csvData].join('\n');
    
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title.replace(/\s+/g, '_').toLowerCase()}_${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  };

  const handleFilterChange = (newFilters) => {
    setFilters(newFilters);
    // Apply filters to data
    let filtered = [...data];
    
    if (newFilters.ivRankMin || newFilters.ivRankMax !== 100) {
      filtered = filtered.filter(row => 
        row.ivRank >= newFilters.ivRankMin && row.ivRank <= newFilters.ivRankMax
      );
    }
    
    if (newFilters.powerMin > 0) {
      filtered = filtered.filter(row => row.power >= newFilters.powerMin);
    }
    
    setFilteredData(filtered);
  };

  return (
    <div className="bg-[#0a0a0b] rounded-lg border border-gray-800 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-white text-lg font-semibold">{title}</h3>
            <HelpCircle className="w-4 h-4 text-gray-500" />
          </div>
          {subtitle && (
            <p className="text-gray-500 text-sm mt-1">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {showFilters && <FilterPanel onFilterChange={handleFilterChange} />}
          <Button
            onClick={exportToCSV}
            variant="outline"
            size="sm"
            className="bg-[#272729] border-gray-700 text-white hover:bg-[#2a2a2c]"
          >
            <Download className="w-4 h-4 mr-2" />
            Export
          </Button>
          {showLikes && (
            <>
              <button className="text-gray-500 hover:text-white transition-colors">
                <ThumbsUp className="w-5 h-5" />
              </button>
              <button className="text-gray-500 hover:text-white transition-colors">
                <ThumbsDown className="w-5 h-5" />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-800">
              {columns.map((col, index) => (
                <th
                  key={index}
                  className="text-left py-3 px-3 text-gray-400 text-xs font-medium uppercase tracking-wider"
                >
                  <div className="flex items-center gap-1">
                    {col.label}
                    {col.sortable && <HelpCircle className="w-3 h-3" />}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredData.map((row, rowIndex) => (
              <tr
                key={rowIndex}
                className="border-b border-gray-800 hover:bg-[#222223] transition-colors"
              >
                {columns.map((col, colIndex) => (
                  <td key={colIndex} className="py-3 px-3 text-sm">
                    {col.key === 'contract' ? (
                      <span className="text-blue-400 font-medium hover:underline cursor-pointer">
                        {row[col.key]}
                      </span>
                    ) : col.key === 'power' ? (
                      <div className="flex items-center gap-2">
                        <div className="w-12 h-2 bg-gray-800 rounded-full overflow-hidden">
                          <div
                            className={`h-full ${getPowerColor(row[col.key])}`}
                            style={{ width: getPowerBarWidth(row[col.key]) }}
                          />
                        </div>
                        <span className="text-gray-300 text-xs">{row[col.key]}%</span>
                      </div>
                    ) : col.key === 'returns' ? (
                      <span className="text-green-400">{row[col.key]}</span>
                    ) : col.key === 'sentiment' ? (
                      <span className="text-red-400">{row[col.key]}</span>
                    ) : col.key === 'aiScore' ? (
                      <span className={row[col.key] >= 50 ? 'text-green-400' : 'text-orange-400'}>
                        {row[col.key]}
                      </span>
                    ) : (
                      <span className="text-gray-300">{row[col.key]}</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Results Info */}
      <div className="mt-4 flex items-center justify-between">
        <p className="text-gray-500 text-sm">
          Showing {filteredData.length} of {data.length} results
        </p>
        <button className="text-blue-400 hover:text-blue-300 text-sm font-medium transition-colors">
          See more →
        </button>
      </div>
    </div>
  );
};

export default DataTable;