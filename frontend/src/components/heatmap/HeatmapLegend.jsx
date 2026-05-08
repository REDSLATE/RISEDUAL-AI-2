import React from 'react';
import { Brain } from 'lucide-react';

const HeatmapLegend = ({ isAI }) => (
  <div className="flex items-center justify-between mt-4 text-[10px] text-slate-400">
    {isAI ? (
      <>
        <div className="flex items-center gap-2">
          {[
            { color: 'bg-red-500', label: '0-25' },
            { color: 'bg-orange-500', label: '25-45' },
            { color: 'bg-gray-500', label: '45-55' },
            { color: 'bg-green-600', label: '55-75' },
            { color: 'bg-green-500', label: '75-100' },
          ].map(({ color, label }) => (
            <div key={label} className="flex items-center gap-1">
              <div className={`w-3 h-3 rounded ${color}`} /><span>{label}</span>
            </div>
          ))}
        </div>
        <span className="flex items-center gap-1"><Brain className="w-3 h-3" /> AI Sentiment Score (0-100)</span>
      </>
    ) : (
      <>
        <div className="flex items-center gap-2">
          {[
            { color: 'bg-red-600', label: '-3%+' },
            { color: 'bg-orange-500', label: '-0.5%' },
            { color: 'bg-yellow-400', label: '+0.5%' },
            { color: 'bg-green-500', label: '+3%+' },
          ].map(({ color, label }) => (
            <div key={label} className="flex items-center gap-1">
              <div className={`w-3 h-3 rounded ${color}`} /><span>{label}</span>
            </div>
          ))}
        </div>
        <span>Tile size ~ S&P 500 sector weight</span>
      </>
    )}
  </div>
);

export default HeatmapLegend;
