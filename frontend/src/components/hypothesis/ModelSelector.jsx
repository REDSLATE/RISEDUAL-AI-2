import React from 'react';
import { Lock, Zap, ChevronDown } from 'lucide-react';
import { Badge } from '../ui/badge';

const ModelSelector = ({ models, selectedModel, onSelect, isPro, onSubscribe, showPicker, setShowPicker }) => {
  const currentModel = models.find(m => m.key === selectedModel) || models[0];

  return (
    <div className="relative" data-testid="model-selector">
      <button
        onClick={() => setShowPicker(!showPicker)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-slate-800/70 border border-slate-400/25 rounded-xl hover:border-slate-600 transition-colors"
        data-testid="model-selector-trigger"
      >
        <div className="flex items-center gap-3">
          <div className={`w-8 h-8 rounded-lg ${currentModel.bg} flex items-center justify-center`}>
            <currentModel.icon className={`w-4 h-4 ${currentModel.color}`} />
          </div>
          <div className="text-left">
            <div className="text-white text-sm font-medium flex items-center gap-2">
              {currentModel.label}
              {currentModel.key === 'consensus' && <Badge className="bg-violet-900/50 text-violet-300 border-violet-700/50 text-[9px] px-1.5">3 MODELS</Badge>}
            </div>
            <div className="text-slate-300 text-xs">{currentModel.provider}</div>
          </div>
        </div>
        <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform ${showPicker ? 'rotate-180' : ''}`} />
      </button>

      {showPicker && (
        <div className="absolute z-[100] mt-1 w-full bg-[#0B1120] border border-slate-600 rounded-xl shadow-2xl shadow-black/60 overflow-hidden" data-testid="model-dropdown">
          {models.map((m) => {
            const ModelIcon = m.icon;
            const locked = !m.free && !isPro;
            return (
              <button
                key={m.key}
                onClick={() => {
                  if (locked) return;
                  onSelect(m.key);
                  setShowPicker(false);
                }}
                className={`w-full flex items-center justify-between gap-3 px-4 py-3 transition-colors ${
                  selectedModel === m.key ? 'bg-[#3DE8D9]/10 border-l-2 border-[#3DE8D9]' : 'border-l-2 border-transparent hover:bg-slate-700/40'
                } ${locked ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}
                data-testid={`model-option-${m.key}`}
                disabled={locked}
              >
                <div className="flex items-center gap-3">
                  <div className={`w-8 h-8 rounded-lg ${m.bg} flex items-center justify-center`}>
                    <ModelIcon className={`w-4 h-4 ${m.color}`} />
                  </div>
                  <div className="text-left">
                    <div className="text-white text-sm font-medium flex items-center gap-2">
                      {m.label}
                      {m.key === 'consensus' && (
                        <Badge className="bg-violet-900/50 text-violet-300 border-violet-700/50 text-[9px] px-1.5">BEST ACCURACY</Badge>
                      )}
                    </div>
                    <div className="text-slate-300 text-xs">{m.provider}</div>
                  </div>
                </div>
                <ModelBadge free={m.free} locked={locked} />
              </button>
            );
          })}
          {!isPro && (
            <div className="px-4 py-2.5 bg-[#0B1120] border-t border-slate-600">
              <button onClick={onSubscribe} className="text-[#3DE8D9] text-xs font-medium hover:underline flex items-center gap-1" data-testid="model-upgrade-btn">
                <Zap className="w-3 h-3" /> Upgrade to Pro to unlock all models + Consensus Mode
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const ModelBadge = ({ free, locked }) => {
  if (free) return <Badge className="bg-slate-700/60 text-slate-400 border-slate-600 text-[9px]">FREE</Badge>;
  if (locked) return <Lock className="w-4 h-4 text-slate-400" />;
  return <Badge className="bg-[#3DE8D9]/20 text-[#3DE8D9] border-[#3DE8D9]/30 text-[9px]">PRO</Badge>;
};

export default ModelSelector;
