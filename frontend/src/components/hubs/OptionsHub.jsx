import React, { useState } from 'react';
import { Radar, Activity, EyeOff } from 'lucide-react';
import OptionsRadar from '../OptionsRadar';
import OptionsFlowScreener from '../OptionsFlowScreener';
import DarkPoolData from '../DarkPoolData';

const TABS = [
  { key: 'radar', label: 'Options Radar', icon: Radar },
  { key: 'flow', label: 'Options Flow', icon: Activity },
  { key: 'darkpool', label: 'Dark Pool', icon: EyeOff },
];

export default function OptionsHub({ onSubscribe, initialTab }) {
  const [tab, setTab] = useState(initialTab || 'radar');

  return (
    <div data-testid="options-hub">
      <div className="flex items-center gap-2 overflow-x-auto pb-1 mb-5 border-b border-slate-700/50">
        {TABS.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-t-lg text-xs font-medium whitespace-nowrap transition-colors ${
                tab === t.key
                  ? 'bg-slate-800 text-[#3DE8D9] border-b-2 border-[#3DE8D9]'
                  : 'text-slate-400 hover:text-white'
              }`}
              data-testid={`options-tab-${t.key}`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      <div className="animate-enter">
        {tab === 'radar' && <OptionsRadar />}
        {tab === 'flow' && <OptionsFlowScreener />}
        {tab === 'darkpool' && <DarkPoolData onSubscribe={onSubscribe} />}
      </div>
    </div>
  );
}
