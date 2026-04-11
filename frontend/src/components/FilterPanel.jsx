import React, { useState } from 'react';
import { Filter, X, RotateCcw } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Label } from './ui/label';
import { Slider } from './ui/slider';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';

const FilterPanel = ({ onFilterChange }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [filters, setFilters] = useState({
    ivRankMin: 0,
    ivRankMax: 100,
    powerMin: 0,
    volumeMin: 0,
    sentiment: 'all',
    timeframe: 'today'
  });

  const handleFilterChange = (key, value) => {
    const updated = { ...filters, [key]: value };
    setFilters(updated);
    onFilterChange(updated);
  };

  const resetFilters = () => {
    const defaultFilters = {
      ivRankMin: 0,
      ivRankMax: 100,
      powerMin: 0,
      volumeMin: 0,
      sentiment: 'all',
      timeframe: 'today'
    };
    setFilters(defaultFilters);
    onFilterChange(defaultFilters);
  };

  return (
    <>
      <Button
        onClick={() => setIsOpen(!isOpen)}
        variant="outline"
        className="bg-[#1E293B] border-slate-600 text-white hover:bg-slate-700"
      >
        <Filter className="w-4 h-4 mr-2" />
        Filters
      </Button>

      {isOpen && (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center">
          <Card className="bg-slate-700/45 border-slate-400/25 rounded-xl p-6 w-full max-w-md">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-white text-lg font-semibold">Filter Options</h3>
              <button
                onClick={() => setIsOpen(false)}
                className="text-slate-400 hover:text-slate-50"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Filter Controls */}
            <div className="space-y-6">
              {/* IV Rank Range */}
              <div>
                <Label className="text-white mb-2 block">
                  IV Rank: {filters.ivRankMin}% - {filters.ivRankMax}%
                </Label>
                <div className="space-y-2">
                  <Slider
                    value={[filters.ivRankMin]}
                    onValueChange={([value]) => handleFilterChange('ivRankMin', value)}
                    max={100}
                    step={5}
                    className="w-full"
                  />
                  <Slider
                    value={[filters.ivRankMax]}
                    onValueChange={([value]) => handleFilterChange('ivRankMax', value)}
                    max={100}
                    step={5}
                    className="w-full"
                  />
                </div>
              </div>

              {/* Power Minimum */}
              <div>
                <Label className="text-white mb-2 block">
                  Minimum Power: {filters.powerMin}%
                </Label>
                <Slider
                  value={[filters.powerMin]}
                  onValueChange={([value]) => handleFilterChange('powerMin', value)}
                  max={100}
                  step={10}
                  className="w-full"
                />
              </div>

              {/* Sentiment */}
              <div>
                <Label className="text-white mb-2 block">Sentiment</Label>
                <Select
                  value={filters.sentiment}
                  onValueChange={(value) => handleFilterChange('sentiment', value)}
                >
                  <SelectTrigger className="bg-[#1E293B] border-slate-600 text-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#060E1F] border-slate-600 shadow-2xl shadow-black/60">
                    <SelectItem value="all" className="text-white">All</SelectItem>
                    <SelectItem value="bullish" className="text-white">Bullish</SelectItem>
                    <SelectItem value="bearish" className="text-white">Bearish</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Timeframe */}
              <div>
                <Label className="text-white mb-2 block">Timeframe</Label>
                <Select
                  value={filters.timeframe}
                  onValueChange={(value) => handleFilterChange('timeframe', value)}
                >
                  <SelectTrigger className="bg-[#1E293B] border-slate-600 text-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#060E1F] border-slate-600 shadow-2xl shadow-black/60">
                    <SelectItem value="today" className="text-white">Today</SelectItem>
                    <SelectItem value="week" className="text-white">This Week</SelectItem>
                    <SelectItem value="month" className="text-white">This Month</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Actions */}
            <div className="flex gap-3 mt-6">
              <Button
                onClick={resetFilters}
                variant="outline"
                className="flex-1 bg-[#1E293B] border-slate-600 text-white hover:bg-slate-700"
              >
                <RotateCcw className="w-4 h-4 mr-2" />
                Reset
              </Button>
              <Button
                onClick={() => setIsOpen(false)}
                className="flex-1 bg-[#3DE8D9] hover:bg-[#7AEEE0]"
              >
                Apply Filters
              </Button>
            </div>
          </Card>
        </div>
      )}
    </>
  );
};

export default FilterPanel;