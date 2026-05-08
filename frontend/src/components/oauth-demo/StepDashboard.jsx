import React from 'react';
import { ArrowRight } from 'lucide-react';
import { DemoNavbar, DemoTicker, NarrationGuide } from './DemoShared';

export const StepDashboard = ({ goToStep }) => (
  <div className="w-full max-w-[1400px] mx-auto flex flex-col h-[calc(100vh-52px)]">
    <DemoNavbar activeTab="Dashboard" />
    <DemoTicker />
    <div className="flex-1 p-6 overflow-y-auto">
      <div className="max-w-4xl mx-auto">
        <div className="grid grid-cols-4 gap-4 mb-6">
          {[
            { label: 'Portfolio Value', val: '$127,450.00', chg: '+2.4%', up: true },
            { label: 'ML Signal', val: 'BULLISH', chg: '67% confidence', up: true },
            { label: 'Paper P&L', val: '+$4,280.50', chg: 'Sharpe 1.56', up: true },
            { label: 'Broker', val: 'Not Connected', chg: 'Setup required', up: false },
          ].map(c => (
            <div key={c.label} className={`bg-slate-900/60 border rounded-xl p-4 ${c.label === 'Broker' ? 'border-amber-500/40 ring-2 ring-amber-500/20' : 'border-slate-700/40'}`}>
              <p className="text-slate-400 text-[10px] uppercase tracking-wider">{c.label}</p>
              <p className={`font-bold text-lg mt-1 ${c.label === 'Broker' ? 'text-amber-400' : 'text-white'}`}>{c.val}</p>
              <p className={`text-xs mt-0.5 ${c.up ? 'text-lime-400' : 'text-amber-400'}`}>{c.chg}</p>
            </div>
          ))}
        </div>
        <div className="flex items-center justify-center mb-4">
          <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl px-6 py-4 flex items-center gap-4">
            <div className="text-amber-400"><ArrowRight className="w-5 h-5" /></div>
            <div>
              <p className="text-white font-medium text-sm">No broker connected — connect one to start trading</p>
              <p className="text-slate-400 text-xs mt-0.5">Navigate to Settings &gt; Broker Connect to link your Alpaca account</p>
            </div>
            <button onClick={() => goToStep('start')} className="bg-[#3DE8D9] hover:bg-[#2fd4c6] text-slate-900 text-sm font-bold px-5 py-2.5 rounded-lg transition-colors shrink-0" data-testid="demo-go-settings">Open Settings</button>
          </div>
        </div>
        <NarrationGuide text={'"Once logged in, they open the Settings area. Here, under Broker Connect, they select Alpaca from the list of available brokers."'} />
      </div>
    </div>
  </div>
);
