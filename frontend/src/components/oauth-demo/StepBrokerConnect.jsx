import React from 'react';
import { ExternalLink, Shield, Lock, CheckCircle } from 'lucide-react';
import { Button } from '../ui/button';
import { DemoNavbar, DemoTicker, SettingsSidebar } from './DemoShared';

export const StepBrokerConnect = ({ goToStep }) => (
  <div className="w-full max-w-[1400px] mx-auto flex flex-col h-[calc(100vh-52px)]">
    <DemoNavbar activeTab="Settings" />
    <DemoTicker />
    <div className="flex flex-1 min-h-0">
      <SettingsSidebar />
      <div className="flex-1 p-6 overflow-y-auto">
        <div className="max-w-2xl">
          <div className="mb-6">
            <h2 className="text-white text-xl font-bold mb-1">Broker Connect</h2>
            <p className="text-slate-400 text-sm">Link your brokerage account to trade directly from RISEDUAL AI</p>
          </div>
          <div className="bg-slate-800/40 border border-slate-700/40 rounded-xl p-4 mb-5">
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <div className="w-2 h-2 rounded-full bg-slate-600" />
              No broker connected — connect one below to start trading
            </div>
          </div>
          <div className="bg-slate-900/80 border border-slate-700/50 rounded-xl overflow-hidden">
            <div className="p-5">
              <div className="flex items-center gap-4 mb-5">
                <div className="w-12 h-12 rounded-xl bg-[#F7D046]/15 flex items-center justify-center shrink-0">
                  <span className="text-[#F7D046] font-bold text-xl">A</span>
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-0.5">
                    <h3 className="text-white font-bold text-lg">Alpaca</h3>
                    <span className="bg-[#3DE8D9]/15 text-[#3DE8D9] text-[10px] px-2 py-0.5 rounded-full font-medium">Recommended</span>
                  </div>
                  <p className="text-slate-400 text-sm">Commission-free, API-first trading for stocks and crypto</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2 mb-5">
                {[
                  { label: 'Stocks & ETFs', sub: 'US markets' },
                  { label: 'Crypto', sub: '24/7 trading' },
                  { label: 'Commission Free', sub: '$0 per trade' },
                  { label: 'Paper Trading', sub: 'Risk-free practice' },
                ].map(f => (
                  <div key={f.label} className="bg-slate-800/50 rounded-lg p-2.5">
                    <p className="text-white text-xs font-medium">{f.label}</p>
                    <p className="text-slate-500 text-[10px]">{f.sub}</p>
                  </div>
                ))}
              </div>
              <Button onClick={() => goToStep('disclosure')} className="w-full bg-gradient-to-r from-[#3DE8D9] to-[#2fd4c6] hover:from-[#2fd4c6] hover:to-[#3B82F6] text-slate-900 text-sm rounded-lg py-3 font-bold" data-testid="demo-oauth-start">
                <ExternalLink className="w-4 h-4 mr-2" />Connect with Alpaca (OAuth)
              </Button>
            </div>
            <div className="bg-slate-800/30 border-t border-slate-700/30 px-5 py-3 space-y-1.5">
              <div className="flex items-center gap-2 text-xs text-slate-400"><Lock className="w-3 h-3 text-[#3DE8D9] shrink-0" /><span>We never see or store your Alpaca password — OAuth2 industry standard</span></div>
              <div className="flex items-center gap-2 text-xs text-slate-400"><Shield className="w-3 h-3 text-[#3DE8D9] shrink-0" /><span>All credentials encrypted with AES-256 in our secure KeyVault</span></div>
              <div className="flex items-center gap-2 text-xs text-slate-400"><CheckCircle className="w-3 h-3 text-[#3DE8D9] shrink-0" /><span>Revoke access anytime from your Alpaca dashboard or RISEDUAL settings</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
);
