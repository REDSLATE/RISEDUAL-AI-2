import React, { useState } from 'react';
import { CheckCircle, Loader2 } from 'lucide-react';
import { StepLanding } from './oauth-demo/StepLanding';
import { StepDashboard } from './oauth-demo/StepDashboard';
import { StepBrokerConnect } from './oauth-demo/StepBrokerConnect';
import { StepDisclosure } from './oauth-demo/StepDisclosure';
import { StepAlpacaAuth } from './oauth-demo/StepAlpacaAuth';
import { StepSuccess, StepRevoke, StepRevoked } from './oauth-demo/StepSuccessRevoke';

const DEMO_STEPS = [
  { id: 'landing', label: 'Sign In' },
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'start', label: 'Broker Connect' },
  { id: 'disclosure', label: 'Disclosure' },
  { id: 'alpaca_auth', label: 'Alpaca Authorization' },
  { id: 'authorizing', label: 'Processing' },
  { id: 'success', label: 'Connected' },
  { id: 'revoke', label: 'Revoke Access' },
];

export default function AlpacaOAuthDemo() {
  const [step, setStep] = useState('landing');
  const [animating, setAnimating] = useState(false);

  const goToStep = (next) => {
    setAnimating(true);
    setTimeout(() => { setStep(next); setAnimating(false); }, 600);
  };

  const handleAuthorize = () => {
    goToStep('authorizing');
    setTimeout(() => { setStep('success'); setAnimating(false); }, 2500);
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
          <button onClick={() => { setStep('landing'); setAnimating(false); }}
            className="text-slate-400 hover:text-white text-xs px-2 py-1 rounded bg-slate-800">Reset</button>
        </div>
      </div>

      <div className={`flex-1 flex items-center justify-center p-6 transition-opacity duration-300 ${animating ? 'opacity-0' : 'opacity-100'}`}>
        {step === 'landing' && <StepLanding goToStep={goToStep} />}
        {step === 'dashboard' && <StepDashboard goToStep={goToStep} />}
        {step === 'start' && <StepBrokerConnect goToStep={goToStep} />}
        {step === 'disclosure' && <StepDisclosure goToStep={goToStep} />}
        {step === 'alpaca_auth' && <StepAlpacaAuth handleAuthorize={handleAuthorize} goToStep={goToStep} />}
        {step === 'authorizing' && (
          <div className="text-center space-y-4">
            <Loader2 className="w-12 h-12 text-[#3DE8D9] animate-spin mx-auto" />
            <div>
              <p className="text-white text-lg font-semibold">Connecting your account...</p>
              <p className="text-slate-400 text-sm mt-1">Securely exchanging authorization tokens</p>
            </div>
          </div>
        )}
        {step === 'success' && <StepSuccess goToStep={goToStep} setStep={setStep} setAnimating={setAnimating} />}
        {step === 'revoke' && <StepRevoke goToStep={goToStep} />}
        {step === 'revoked' && <StepRevoked setStep={setStep} setAnimating={setAnimating} />}
      </div>
    </div>
  );
}
