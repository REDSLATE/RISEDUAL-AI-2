import React, { useState } from 'react';
import { ThumbsUp, ThumbsDown, HelpCircle, Download } from 'lucide-react';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import FilterPanel from './FilterPanel';
import QuickTrade from './QuickTrade';

const DataTable = ({ title, subtitle, columns, data, showLikes = true, showFilters = false, showTrading = true }) => {
  const [filteredData, setFilteredData] = useState(data);
  const [filters, setFilters] = useState(null);

  const getPowerColor = (power) => {
    if (power >= 80) return 'bg-emerald-500';
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

const getCellContent = (col, row) => {
  const value = row[col.key];
  switch (col.key) {
    case 'contract':
      return (
        <span
          onClick={() => toast.info(`Details for ${value}: Contract info, price history, volume analysis, Greeks`)}
          className="text-blue-400 font-medium hover:underline cursor-pointer"
        >
          {value}
        </span>
      );
    case 'power':
      return (
        <div className="flex items-center gap-2">
          <div className="w-12 h-2 bg-slate-700 rounded-full overflow-hidden">
            <div className={`h-full ${getPowerColor(value)}`} style={{ width: getPowerBarWidth(value) }} />
          </div>
          <span className="text-slate-300 text-xs">{value}%</span>
        </div>
      );
    case 'returns':
      return <span className="text-lime-400">{value}</span>;
    case 'sentiment':
      return <span className="text-orange-400">{value}</span>;
    case 'aiScore':
      return <span className={value >= 50 ? 'text-lime-400' : 'text-orange-400'}>{value}</span>;
    default:
      return <span className="text-slate-300">{value}</span>;
  }
};

  return (
    <div className="bg-slate-700/45 rounded-xl border border-slate-400/25 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-white text-lg font-semibold">{title}</h3>
            <HelpCircle className="w-4 h-4 text-slate-400" />
          </div>
          {subtitle && (
            <p className="text-slate-300 text-sm mt-1">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {showFilters && <FilterPanel onFilterChange={handleFilterChange} />}
          <Button
            onClick={exportToCSV}
            variant="outline"
            size="sm"
            className="bg-[#1E293B] border-slate-600 text-white hover:bg-slate-700"
          >
            <Download className="w-4 h-4 mr-2" />
            Export
          </Button>
          {showLikes && (
            <>
              <button 
                onClick={() => toast.success('Thanks for your feedback!')}
                className="text-slate-400 hover:text-lime-400 transition-colors"
              >
                <ThumbsUp className="w-5 h-5" />
              </button>
              <button 
                onClick={() => toast('Thanks for your feedback! We\'ll improve this.')}
                className="text-slate-400 hover:text-orange-400 transition-colors"
              >
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
            <tr className="border-b border-slate-400/30">
              {columns.map((col) => (
                <th
                  key={col.key || col.label}
                  className="text-left py-3 px-3 text-slate-300 text-xs font-medium uppercase tracking-wider"
                >
                  <div className="flex items-center gap-1">
                    {col.label}
                    {col.sortable && <HelpCircle className="w-3 h-3" />}
                  </div>
                </th>
              ))}
              {showTrading && (
                <th className="text-left py-3 px-3 text-slate-300 text-xs font-medium uppercase tracking-wider">
                  QUICK TRADE
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {filteredData.map((row, rowIndex) => (
              <tr
                key={row.contract || row.symbol || rowIndex}
                className="border-b border-slate-400/30 hover:bg-slate-700 transition-colors"
              >
                {columns.map((col) => (
                  <td key={col.key || col.label} className="py-3 px-3 text-sm">
                    {getCellContent(col, row)}
                  </td>
                ))}
                {showTrading && (
                  <td className="py-3 px-3">
                    <QuickTrade symbol={row.contract} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Results Info */}
      <div className="mt-4 flex items-center justify-between">
        <p className="text-slate-300 text-sm">
          Showing {filteredData.length} of {data.length} results
        </p>
        <button 
          onClick={() => toast.info('Full data view coming soon!')}
          className="text-blue-400 hover:text-blue-300 text-sm font-medium transition-colors"
        >
          See more →
        </button>
      </div>
    </div>
  );
};

export default DataTable;