import React from 'react';
import { CheckCircle, Shield, Lock, AlertTriangle } from 'lucide-react';
import { Button } from '../ui/button';
import { DemoNavbar, DemoTicker, SettingsSidebar, NarrationGuide } from './DemoShared';

export const StepSuccess = ({ goToStep, setStep, setAnimating }) => (
  <div className="max-w-xl w-full">
    <div className="bg-slate-900 rounded-2xl border border-emerald-700/40 overflow-hidden">
      <div className="bg-emerald-900/20 px-6 py-4 border-b border-emerald-700/30 flex items-center gap-3">
        <CheckCircle className="w-6 h-6 text-emerald-400" />
        <div>
          <h2 className="text-white font-bold">Alpaca Connected Successfully</h2>
          <p className="text-emerald-400/70 text-xs">OAuth authorization complete — your account is now linked</p>
        </div>
      </div>
      <div className="p-6 space-y-4">
        <div className="grid grid-cols-2 gap-3">
          {[
            { label: 'Account', val: 'Paper Trading', color: 'text-white' },
            { label: 'Buying Power', val: '$100,000.00', color: 'text-white' },
            { label: 'Auth Method', val: 'OAuth 2.0', color: 'text-emerald-400', icon: <Shield className="w-3 h-3" /> },
            { label: 'Status', val: 'Active', color: 'text-emerald-400', icon: <CheckCircle className="w-3 h-3" /> },
          ].map(c => (
            <div key={c.label} className="bg-slate-800/60 rounded-lg p-3">
              <p className="text-slate-400 text-[10px] uppercase tracking-wider">{c.label}</p>
              <p className={`font-semibold text-sm mt-1 flex items-center gap-1 ${c.color}`}>{c.icon}{c.val}</p>
            </div>
          ))}
        </div>
        <div className="bg-slate-800/40 rounded-lg p-4">
          <h3 className="text-white font-medium text-sm mb-2">What you can now do:</h3>
          <ul className="space-y-1.5 text-xs text-slate-300">
            {['View portfolio and positions in real-time', 'Execute trades powered by AI signals', 'Sync watchlist with your brokerage', 'Enable autonomous ML-driven paper trading'].map(t => (
              <li key={t} className="flex items-center gap-2"><CheckCircle className="w-3 h-3 text-[#3DE8D9]" />{t}</li>
            ))}
          </ul>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500"><Lock className="w-3 h-3" /><span>You can disconnect anytime from Settings or your Alpaca dashboard</span></div>
        <div className="flex gap-3">
          <button onClick={() => goToStep('revoke')} className="flex-1 py-2.5 rounded-lg border border-red-500/40 text-red-400 text-sm font-medium hover:bg-red-500/10 transition-colors" data-testid="demo-disconnect">Disconnect Alpaca</button>
          <Button onClick={() => { setStep('landing'); setAnimating(false); }} className="flex-1 bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-lg py-2.5" data-testid="demo-restart">Replay Demo</Button>
        </div>
        <NarrationGuide text={'"The user is redirected back to RISEDUAL and Alpaca is shown as connected and ready to power trading services."'} />
      </div>
    </div>
  </div>
);

export const StepRevoke = ({ goToStep }) => (
  <div className="w-full max-w-[1400px] mx-auto flex flex-col h-[calc(100vh-52px)]">
    <DemoNavbar activeTab="Settings" />
    <DemoTicker />
    <div className="flex flex-1 min-h-0">
      <SettingsSidebar />
      <div className="flex-1 p-6 overflow-y-auto">
        <div className="max-w-2xl">
          <div className="mb-6">
            <h2 className="text-white text-xl font-bold mb-1">Broker Connect</h2>
            <p className="text-slate-400 text-sm">Manage your connected brokerage accounts</p>
          </div>
          <div className="bg-slate-900/80 border border-slate-700/50 rounded-xl overflow-hidden mb-5">
            <div className="p-5">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-[#F7D046]/15 flex items-center justify-center"><span className="text-[#F7D046] font-bold text-lg">A</span></div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-white font-semibold">Alpaca</h3>
                      <span className="bg-emerald-500/15 text-emerald-400 text-[10px] px-2 py-0.5 rounded-full font-medium flex items-center gap-1"><CheckCircle className="w-2.5 h-2.5" /> Connected</span>
                    </div>
                    <p className="text-slate-400 text-xs">Paper Trading · OAuth 2.0 · Connected today</p>
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3 mb-4">
                {[
                  { label: 'Buying Power', val: '$100,000.00', color: 'text-white' },
                  { label: 'Auth Method', val: 'OAuth 2.0', color: 'text-emerald-400' },
                  { label: 'Status', val: 'Active', color: 'text-emerald-400' },
                ].map(c => (
                  <div key={c.label} className="bg-slate-800/50 rounded-lg p-2.5">
                    <p className="text-slate-500 text-[10px] uppercase">{c.label}</p>
                    <p className={`text-sm font-medium mt-0.5 ${c.color}`}>{c.val}</p>
                  </div>
                ))}
              </div>
              <div className="bg-red-500/5 border border-red-500/20 rounded-xl p-4">
                <div className="flex items-start gap-3 mb-4">
                  <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                  <div>
                    <h4 className="text-red-400 font-semibold text-sm mb-1">Disconnect Alpaca Account?</h4>
                    <p className="text-slate-400 text-xs leading-relaxed">This will revoke RISEDUAL AI's access to your Alpaca account. All active orders placed through RISEDUAL will remain open on Alpaca. Autonomous trading will be paused. You can reconnect at any time.</p>
                  </div>
                </div>
                <div className="flex gap-3">
                  <button onClick={() => goToStep('success')} className="flex-1 py-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 text-sm font-medium hover:bg-slate-700 transition-colors">Cancel</button>
                  <button onClick={() => goToStep('revoked')} className="flex-1 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm font-bold transition-colors" data-testid="demo-confirm-revoke">Confirm Disconnect</button>
                </div>
              </div>
            </div>
          </div>
          <div className="bg-slate-800/40 border border-slate-700/40 rounded-xl p-4">
            <h4 className="text-white font-medium text-sm mb-2">Alternative: Revoke from Alpaca</h4>
            <p className="text-slate-400 text-xs leading-relaxed mb-3">You can also revoke RISEDUAL AI's access directly from your Alpaca account at <span className="text-[#3DE8D9] ml-1">app.alpaca.markets &gt; Settings &gt; Connected Apps &gt; RISEDUAL AI &gt; Revoke</span></p>
            <div className="flex items-center gap-2 text-xs text-slate-500"><Shield className="w-3 h-3 text-[#3DE8D9]" /><span>Either method immediately revokes all access — no waiting period</span></div>
          </div>
          <NarrationGuide text={'"To revoke access, the user navigates to Settings > Broker Connect and clicks Disconnect. They can also revoke directly from their Alpaca dashboard under Connected Apps."'} />
        </div>
      </div>
    </div>
  </div>
);

export const StepRevoked = ({ setStep, setAnimating }) => (
  <div className="max-w-xl w-full">
    <div className="bg-slate-900 rounded-2xl border border-slate-700/50 overflow-hidden">
      <div className="bg-slate-800/60 px-6 py-4 border-b border-slate-700/50 flex items-center gap-3">
        <Shield className="w-6 h-6 text-slate-400" />
        <div><h2 className="text-white font-bold">Alpaca Disconnected</h2><p className="text-slate-400 text-xs">OAuth access has been revoked</p></div>
      </div>
      <div className="p-6 space-y-4">
        <div className="bg-slate-800/40 rounded-lg p-4 space-y-2.5">
          {['OAuth tokens revoked — RISEDUAL AI can no longer access your Alpaca account', 'Autonomous trading paused', 'Existing open orders on Alpaca are unaffected', 'No stored credentials remain — KeyVault entry deleted'].map(t => (
            <div key={t} className="flex items-center gap-2 text-sm text-slate-300"><CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" /><span>{t}</span></div>
          ))}
        </div>
        <div className="bg-slate-800/30 rounded-lg p-3"><p className="text-slate-400 text-xs">You can reconnect your Alpaca account at any time by returning to Settings &gt; Broker Connect.</p></div>
        <Button onClick={() => { setStep('landing'); setAnimating(false); }} className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-lg py-2.5">Replay Full Demo</Button>
        <NarrationGuide text={'"Access is immediately revoked. All OAuth tokens are deleted and no credentials remain stored. The user can reconnect at any time."'} />
      </div>
    </div>
  </div>
);
