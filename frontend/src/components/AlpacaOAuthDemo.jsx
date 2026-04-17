import React, { useState, useEffect } from 'react';
import { Shield, CheckCircle, ExternalLink, Lock, ArrowRight, Loader2 } from 'lucide-react';
import { Button } from './ui/button';
import { getApiBase } from '../utils/apiBase';

/**
 * AlpacaOAuthDemo — Simulates the Alpaca OAuth authorization flow
 * for compliance video recording. Shows the full user journey:
 * 1. User clicks "Connect with Alpaca" on RISEDUAL
 * 2. Redirected to Alpaca's authorization page (simulated)
 * 3. User reviews permissions and clicks Authorize
 * 4. Redirected back to RISEDUAL with success
 */

const DEMO_STEPS = [
  { id: 'start', label: 'RISEDUAL Dashboard' },
  { id: 'alpaca_auth', label: 'Alpaca Authorization' },
  { id: 'authorizing', label: 'Processing' },
  { id: 'success', label: 'Connected' },
];

export default function AlpacaOAuthDemo() {
  const [step, setStep] = useState('start');
  const [animating, setAnimating] = useState(false);

  const goToStep = (next) => {
    setAnimating(true);
    setTimeout(() => {
      setStep(next);
      setAnimating(false);
    }, 600);
  };

  const handleAuthorize = () => {
    goToStep('authorizing');
    setTimeout(() => {
      setStep('success');
      setAnimating(false);
    }, 2500);
  };

  return (
    <div className="min-h-screen bg-[#060E1F] flex flex-col" data-testid="oauth-demo">
      {/* Progress bar */}
      <div className="bg-slate-900 border-b border-slate-700/50 px-6 py-3">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <span className="text-white font-bold text-sm">OAuth Flow Demo</span>
          <div className="flex items-center gap-2">
            {DEMO_STEPS.map((s, i) => (
              <React.Fragment key={s.id}>
                <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors ${
                  step === s.id ? 'bg-[#3DE8D9]/20 text-[#3DE8D9]' : 
                  DEMO_STEPS.findIndex(x => x.id === step) > i ? 'bg-emerald-900/30 text-emerald-400' : 'bg-slate-800 text-slate-500'
                }`}>
                  {DEMO_STEPS.findIndex(x => x.id === step) > i && <CheckCircle className="w-3 h-3" />}
                  {s.label}
                </div>
                {i < DEMO_STEPS.length - 1 && <div className="w-6 h-px bg-slate-700" />}
              </React.Fragment>
            ))}
          </div>
          <button onClick={() => { setStep('start'); setAnimating(false); }}
            className="text-slate-400 hover:text-white text-xs px-2 py-1 rounded bg-slate-800">
            Reset
          </button>
        </div>
      </div>

      <div className={`flex-1 flex items-center justify-center p-6 transition-opacity duration-300 ${animating ? 'opacity-0' : 'opacity-100'}`}>

        {/* Step 1: Full RISEDUAL Dashboard with Broker Connect */}
        {step === 'start' && (
          <div className="w-full max-w-[1400px] mx-auto flex flex-col h-[calc(100vh-52px)]">
            {/* Simulated Navbar */}
            <div className="bg-slate-900/90 border-b border-slate-700/50 px-4 py-2.5 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-6">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-lg bg-[#3DE8D9]/20 flex items-center justify-center">
                    <span className="text-[#3DE8D9] font-bold text-xs">R</span>
                  </div>
                  <span className="text-white font-bold text-sm">RISEDUAL AI</span>
                </div>
                <div className="hidden md:flex items-center gap-1">
                  {['Dashboard', 'Research', 'Workspace'].map(t => (
                    <span key={t} className="px-3 py-1.5 rounded-lg text-slate-400 text-xs hover:text-white transition-colors cursor-default">{t}</span>
                  ))}
                  <span className="px-3 py-1.5 rounded-lg text-white bg-slate-800 text-xs font-medium">Settings</span>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-[#3DE8D9] text-xs font-medium px-2 py-1 rounded bg-[#3DE8D9]/10">PRO</span>
                <div className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center">
                  <span className="text-white text-xs font-medium">JD</span>
                </div>
              </div>
            </div>

            {/* Simulated Ticker */}
            <div className="bg-[#060E1F] border-b border-slate-700/40 px-4 py-1.5 flex items-center gap-6 overflow-hidden shrink-0">
              {[
                { s: 'AAPL', p: '213.25', c: '+1.42%' },
                { s: 'TSLA', p: '178.90', c: '-0.83%' },
                { s: 'NVDA', p: '892.10', c: '+2.15%' },
                { s: 'MSFT', p: '425.80', c: '+0.67%' },
                { s: 'AMZN', p: '186.40', c: '+1.08%' },
                { s: 'GOOGL', p: '176.55', c: '-0.22%' },
                { s: 'META', p: '505.30', c: '+1.95%' },
                { s: 'SPY', p: '533.20', c: '+0.48%' },
              ].map(t => (
                <div key={t.s} className="flex items-center gap-2 text-xs whitespace-nowrap">
                  <span className="text-white font-medium">{t.s}</span>
                  <span className="text-slate-300">${t.p}</span>
                  <span className={t.c.startsWith('+') ? 'text-lime-400' : 'text-orange-400'}>{t.c}</span>
                </div>
              ))}
            </div>

            {/* Main Content: Settings > Broker Connect */}
            <div className="flex flex-1 min-h-0">
              {/* Settings Sidebar */}
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

              {/* Broker Connect Main Panel */}
              <div className="flex-1 p-6 overflow-y-auto">
                <div className="max-w-2xl">
                  <div className="mb-6">
                    <h2 className="text-white text-xl font-bold mb-1">Broker Connect</h2>
                    <p className="text-slate-400 text-sm">Link your brokerage account to trade directly from RISEDUAL AI</p>
                  </div>

                  {/* Connected status (none) */}
                  <div className="bg-slate-800/40 border border-slate-700/40 rounded-xl p-4 mb-5">
                    <div className="flex items-center gap-2 text-sm text-slate-400">
                      <div className="w-2 h-2 rounded-full bg-slate-600" />
                      No broker connected — connect one below to start trading
                    </div>
                  </div>

                  {/* Alpaca broker card */}
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

                      {/* Features grid */}
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

                      <Button
                        onClick={() => goToStep('alpaca_auth')}
                        className="w-full bg-gradient-to-r from-[#3DE8D9] to-[#2fd4c6] hover:from-[#2fd4c6] hover:to-[#3B82F6] text-slate-900 text-sm rounded-lg py-3 font-bold"
                        data-testid="demo-oauth-start"
                      >
                        <ExternalLink className="w-4 h-4 mr-2" />
                        Connect with Alpaca (OAuth)
                      </Button>
                    </div>

                    {/* Security footer */}
                    <div className="bg-slate-800/30 border-t border-slate-700/30 px-5 py-3 space-y-1.5">
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <Lock className="w-3 h-3 text-[#3DE8D9] shrink-0" />
                        <span>We never see or store your Alpaca password — OAuth2 industry standard</span>
                      </div>
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <Shield className="w-3 h-3 text-[#3DE8D9] shrink-0" />
                        <span>All credentials encrypted with AES-256 in our secure KeyVault</span>
                      </div>
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <CheckCircle className="w-3 h-3 text-[#3DE8D9] shrink-0" />
                        <span>Revoke access anytime from your Alpaca dashboard or RISEDUAL settings</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Step 2: Simulated Alpaca Authorization Page */}
        {step === 'alpaca_auth' && (
          <div className="max-w-md w-full">
            <div className="bg-white rounded-2xl overflow-hidden shadow-2xl">
              {/* Alpaca header */}
              <div className="bg-[#1A1A2E] px-6 py-5 flex items-center justify-center gap-3">
                <div className="w-8 h-8 rounded-full bg-[#F7D046] flex items-center justify-center">
                  <span className="text-[#1A1A2E] font-bold text-sm">A</span>
                </div>
                <span className="text-white font-bold text-lg">Alpaca</span>
              </div>

              <div className="p-6 space-y-5">
                <div className="text-center">
                  <h2 className="text-gray-900 text-lg font-bold mb-1">Authorize RISEDUAL AI</h2>
                  <p className="text-gray-500 text-sm">risedual.ai wants to access your Alpaca account</p>
                </div>

                {/* Permissions */}
                <div className="bg-gray-50 rounded-xl p-4 space-y-3">
                  <p className="text-gray-700 text-xs font-semibold uppercase tracking-wider">This app will be able to:</p>
                  <div className="space-y-2.5">
                    <div className="flex items-start gap-2">
                      <CheckCircle className="w-4 h-4 text-green-500 mt-0.5 shrink-0" />
                      <div>
                        <p className="text-gray-800 text-sm font-medium">View your account information</p>
                        <p className="text-gray-500 text-xs">Portfolio value, positions, and buying power</p>
                      </div>
                    </div>
                    <div className="flex items-start gap-2">
                      <CheckCircle className="w-4 h-4 text-green-500 mt-0.5 shrink-0" />
                      <div>
                        <p className="text-gray-800 text-sm font-medium">Place and manage trades</p>
                        <p className="text-gray-500 text-xs">Submit, modify, and cancel orders on your behalf</p>
                      </div>
                    </div>
                    <div className="flex items-start gap-2">
                      <CheckCircle className="w-4 h-4 text-green-500 mt-0.5 shrink-0" />
                      <div>
                        <p className="text-gray-800 text-sm font-medium">Access market data</p>
                        <p className="text-gray-500 text-xs">Real-time quotes, historical data, and news</p>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Security note */}
                <div className="flex items-center gap-2 text-xs text-gray-500 bg-blue-50 rounded-lg p-3">
                  <Lock className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                  <span>RISEDUAL AI will <strong>not</strong> have access to your password or the ability to withdraw funds.</span>
                </div>

                {/* Action buttons */}
                <div className="flex gap-3">
                  <button
                    onClick={() => goToStep('start')}
                    className="flex-1 py-2.5 rounded-lg border border-gray-300 text-gray-600 text-sm font-medium hover:bg-gray-50 transition-colors"
                  >
                    Deny
                  </button>
                  <button
                    onClick={handleAuthorize}
                    className="flex-1 py-2.5 rounded-lg bg-[#F7D046] text-[#1A1A2E] text-sm font-bold hover:bg-[#F7D046]/90 transition-colors flex items-center justify-center gap-2"
                    data-testid="demo-authorize-btn"
                  >
                    Authorize
                    <ArrowRight className="w-4 h-4" />
                  </button>
                </div>

                <p className="text-center text-gray-400 text-[10px]">
                  By authorizing, you agree to Alpaca's Terms of Service
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Step 3: Processing */}
        {step === 'authorizing' && (
          <div className="text-center space-y-4">
            <Loader2 className="w-12 h-12 text-[#3DE8D9] animate-spin mx-auto" />
            <div>
              <p className="text-white text-lg font-semibold">Connecting your account...</p>
              <p className="text-slate-400 text-sm mt-1">Securely exchanging authorization tokens</p>
            </div>
          </div>
        )}

        {/* Step 4: Success */}
        {step === 'success' && (
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
                {/* Simulated account info */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-slate-800/60 rounded-lg p-3">
                    <p className="text-slate-400 text-[10px] uppercase tracking-wider">Account</p>
                    <p className="text-white font-semibold text-sm mt-1">Paper Trading</p>
                  </div>
                  <div className="bg-slate-800/60 rounded-lg p-3">
                    <p className="text-slate-400 text-[10px] uppercase tracking-wider">Buying Power</p>
                    <p className="text-white font-semibold text-sm mt-1">$100,000.00</p>
                  </div>
                  <div className="bg-slate-800/60 rounded-lg p-3">
                    <p className="text-slate-400 text-[10px] uppercase tracking-wider">Auth Method</p>
                    <p className="text-emerald-400 font-semibold text-sm mt-1 flex items-center gap-1">
                      <Shield className="w-3 h-3" /> OAuth 2.0
                    </p>
                  </div>
                  <div className="bg-slate-800/60 rounded-lg p-3">
                    <p className="text-slate-400 text-[10px] uppercase tracking-wider">Status</p>
                    <p className="text-emerald-400 font-semibold text-sm mt-1 flex items-center gap-1">
                      <CheckCircle className="w-3 h-3" /> Active
                    </p>
                  </div>
                </div>

                {/* What's next */}
                <div className="bg-slate-800/40 rounded-lg p-4">
                  <h3 className="text-white font-medium text-sm mb-2">What you can now do:</h3>
                  <ul className="space-y-1.5 text-xs text-slate-300">
                    <li className="flex items-center gap-2"><CheckCircle className="w-3 h-3 text-[#3DE8D9]" />View portfolio and positions in real-time</li>
                    <li className="flex items-center gap-2"><CheckCircle className="w-3 h-3 text-[#3DE8D9]" />Execute trades powered by AI signals</li>
                    <li className="flex items-center gap-2"><CheckCircle className="w-3 h-3 text-[#3DE8D9]" />Sync watchlist with your brokerage</li>
                    <li className="flex items-center gap-2"><CheckCircle className="w-3 h-3 text-[#3DE8D9]" />Enable autonomous ML-driven paper trading</li>
                  </ul>
                </div>

                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <Lock className="w-3 h-3" />
                  <span>You can disconnect anytime from Settings or your Alpaca dashboard</span>
                </div>

                <Button onClick={() => goToStep('start')}
                  className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-lg py-2.5"
                  data-testid="demo-restart"
                >
                  Replay Demo
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
