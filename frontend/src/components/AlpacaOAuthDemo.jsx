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

        {/* Step 1: RISEDUAL Connect Broker */}
        {step === 'start' && (
          <div className="max-w-xl w-full">
            <div className="bg-slate-900 rounded-2xl border border-slate-700/50 overflow-hidden">
              {/* Simulated RISEDUAL header */}
              <div className="bg-slate-800/60 px-6 py-4 border-b border-slate-700/50 flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[#3DE8D9]/20 flex items-center justify-center">
                  <span className="text-[#3DE8D9] font-bold text-sm">R</span>
                </div>
                <span className="text-white font-bold">RISEDUAL AI</span>
                <span className="text-slate-500 text-xs ml-auto">Connect Broker</span>
              </div>

              <div className="p-6 space-y-5">
                <div>
                  <h2 className="text-white text-xl font-bold mb-1">Connect Your Alpaca Account</h2>
                  <p className="text-slate-400 text-sm">Link your brokerage to trade directly from RISEDUAL AI</p>
                </div>

                {/* Alpaca broker card */}
                <div className="bg-slate-800/60 border border-slate-700/40 rounded-xl p-4">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl bg-[#F7D046]/15 flex items-center justify-center">
                      <span className="text-[#F7D046] font-bold text-lg">A</span>
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-white font-semibold">Alpaca</h3>
                        <span className="bg-[#3DE8D9]/20 text-[#3DE8D9] text-[10px] px-2 py-0.5 rounded-full">Recommended</span>
                      </div>
                      <p className="text-slate-400 text-xs">Commission-free API-first trading for stocks & crypto</p>
                    </div>
                  </div>

                  <Button
                    onClick={() => goToStep('alpaca_auth')}
                    className="w-full bg-gradient-to-r from-[#3DE8D9] to-[#7AEEE0] hover:from-[#7AEEE0] hover:to-[#3B82F6] text-white text-sm rounded-lg py-3 font-semibold"
                    data-testid="demo-oauth-start"
                  >
                    <ExternalLink className="w-4 h-4 mr-2" />
                    Connect with Alpaca (OAuth)
                  </Button>

                  <div className="flex items-center gap-2 mt-3 text-xs text-slate-500">
                    <Shield className="w-3.5 h-3.5" />
                    <span>You'll be redirected to Alpaca's secure login page</span>
                  </div>
                </div>

                {/* Security info */}
                <div className="bg-slate-800/40 rounded-lg p-3 space-y-2">
                  <div className="flex items-center gap-2 text-xs text-slate-400">
                    <Lock className="w-3 h-3 text-[#3DE8D9]" />
                    <span>We never see or store your Alpaca password</span>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-slate-400">
                    <Shield className="w-3 h-3 text-[#3DE8D9]" />
                    <span>OAuth2 authorization — industry standard security</span>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-slate-400">
                    <CheckCircle className="w-3 h-3 text-[#3DE8D9]" />
                    <span>Revoke access anytime from your Alpaca dashboard</span>
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
