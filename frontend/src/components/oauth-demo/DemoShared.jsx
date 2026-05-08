import React from 'react';
import { ExternalLink } from 'lucide-react';

const TICKER_DATA = [
  { s: 'AAPL', p: '213.25', c: '+1.42%' },
  { s: 'TSLA', p: '178.90', c: '-0.83%' },
  { s: 'NVDA', p: '892.10', c: '+2.15%' },
  { s: 'MSFT', p: '425.80', c: '+0.67%' },
  { s: 'SPY', p: '533.20', c: '+0.48%' },
];

export const DemoNavbar = ({ activeTab = 'Dashboard' }) => (
  <div className="bg-slate-900/90 border-b border-slate-700/50 px-4 py-2.5 flex items-center justify-between shrink-0">
    <div className="flex items-center gap-6">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-[#3DE8D9]/20 flex items-center justify-center">
          <span className="text-[#3DE8D9] font-bold text-xs">R</span>
        </div>
        <span className="text-white font-bold text-sm">RISEDUAL AI</span>
      </div>
      <div className="hidden md:flex items-center gap-1">
        {['Dashboard', 'Research', 'Workspace', 'Settings'].map(t => (
          <span key={t} className={`px-3 py-1.5 rounded-lg text-xs ${t === activeTab ? 'text-white bg-slate-800 font-medium' : 'text-slate-400 cursor-default'}`}>{t}</span>
        ))}
      </div>
    </div>
    <div className="flex items-center gap-3">
      <span className="text-[#3DE8D9] text-xs font-medium px-2 py-1 rounded bg-[#3DE8D9]/10">PRO</span>
      <div className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center">
        <span className="text-white text-xs font-medium">JD</span>
      </div>
    </div>
  </div>
);

export const DemoTicker = () => (
  <div className="bg-[#060E1F] border-b border-slate-700/40 px-4 py-1.5 flex items-center gap-6 overflow-hidden shrink-0">
    {TICKER_DATA.map(t => (
      <div key={t.s} className="flex items-center gap-2 text-xs whitespace-nowrap">
        <span className="text-white font-medium">{t.s}</span>
        <span className="text-slate-300">${t.p}</span>
        <span className={t.c.startsWith('+') ? 'text-lime-400' : 'text-orange-400'}>{t.c}</span>
      </div>
    ))}
  </div>
);

export const SettingsSidebar = () => (
  <div className="w-56 bg-slate-900/60 border-r border-slate-700/40 p-4 shrink-0 hidden md:block">
    <h3 className="text-slate-400 text-[10px] uppercase tracking-wider mb-3 font-medium">Settings</h3>
    <div className="space-y-0.5">
      {['Profile', 'Subscription', 'Notifications', 'API Keys'].map(item => (
        <div key={item} className="px-3 py-2 rounded-lg text-slate-400 text-xs cursor-default hover:bg-slate-800/40">{item}</div>
      ))}
      <div className="px-3 py-2 rounded-lg text-white text-xs bg-slate-800/70 font-medium flex items-center gap-2">
        <ExternalLink className="w-3 h-3 text-[#3DE8D9]" />
        Broker Connect
      </div>
      {['Security', 'Appearance'].map(item => (
        <div key={item} className="px-3 py-2 rounded-lg text-slate-400 text-xs cursor-default hover:bg-slate-800/40">{item}</div>
      ))}
    </div>
  </div>
);

export const NarrationGuide = ({ text }) => (
  <div className="mt-4 bg-blue-500/10 border border-blue-500/20 rounded-lg px-4 py-2.5">
    <p className="text-blue-400 text-xs italic text-center">{text}</p>
  </div>
);
