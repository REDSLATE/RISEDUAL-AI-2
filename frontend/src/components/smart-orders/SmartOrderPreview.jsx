import React from 'react';
import { Eye } from 'lucide-react';

const SmartOrderPreview = ({ preview }) => {
  if (!preview) return null;
  return (
    <div className="bg-[#111C30] rounded-xl p-4 border border-[#3DE8D9]/30" data-testid="smart-order-preview">
      <div className="flex items-center gap-2 mb-3">
        <Eye className="w-4 h-4 text-[#3DE8D9]" />
        <span className="text-white text-xs font-semibold">Order Preview</span>
      </div>
      <div className="grid grid-cols-3 gap-3 text-center">
        <div>
          <p className="text-slate-400 text-[9px]">Entry Price</p>
          <p className="text-white text-sm font-bold">${preview.avg_entry_price}</p>
        </div>
        <div>
          <p className="text-slate-400 text-[9px]">Projected Risk</p>
          <p className="text-red-400 text-sm font-bold">${preview.projected_risk}</p>
        </div>
        <div>
          <p className="text-slate-400 text-[9px]">Projected Reward</p>
          <p className="text-lime-400 text-sm font-bold">${preview.projected_reward}</p>
        </div>
      </div>
      <div className="flex items-center justify-center mt-2">
        <span className="text-slate-400 text-[10px]">Risk:Reward</span>
        <span className={`ml-2 text-lg font-black ${preview.risk_reward_ratio >= 2 ? 'text-lime-400' : preview.risk_reward_ratio >= 1 ? 'text-amber-400' : 'text-red-400'}`}>
          {preview.risk_reward_ratio > 0 ? `1:${preview.risk_reward_ratio}` : 'N/A'}
        </span>
      </div>
      {preview.legs?.length > 1 && (
        <div className="mt-3 border-t border-slate-700 pt-2">
          <p className="text-slate-400 text-[9px] mb-1">Ladder Legs ({preview.leg_count})</p>
          {preview.legs.map((leg, i) => (
            <div key={`preview-leg-${i}`} className="flex justify-between text-[10px]">
              <span className="text-slate-300">${leg.price.toFixed(2)}</span>
              <span className="text-slate-400">{leg.qty.toFixed(2)} shares</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default SmartOrderPreview;
