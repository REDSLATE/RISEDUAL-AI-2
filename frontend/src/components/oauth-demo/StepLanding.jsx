import React from 'react';

export const StepLanding = ({ goToStep }) => (
  <div className="w-full max-w-[1400px] mx-auto flex flex-col h-[calc(100vh-52px)]">
    <div className="bg-slate-900/90 border-b border-slate-700/50 px-6 py-3 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-[#3DE8D9]/20 flex items-center justify-center">
          <span className="text-[#3DE8D9] font-bold text-sm">R</span>
        </div>
        <span className="text-white font-bold">RISEDUAL AI</span>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-slate-400 text-sm cursor-default">Features</span>
        <span className="text-slate-400 text-sm cursor-default">Pricing</span>
        <button onClick={() => goToStep('dashboard')} className="bg-[#3DE8D9] hover:bg-[#2fd4c6] text-slate-900 text-sm font-bold px-5 py-2 rounded-lg transition-colors" data-testid="demo-sign-in">
          Sign In
        </button>
      </div>
    </div>
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center max-w-lg">
        <h1 className="text-white text-4xl font-bold mb-3">RISEDUAL AI</h1>
        <p className="text-slate-400 text-lg mb-2">AI-Powered Trading Intelligence</p>
        <p className="text-slate-500 text-sm mb-8">ML signals with Sharpe 1.56 · Autonomous paper trading · SEC EDGAR fundamentals</p>
        <div className="bg-slate-800/60 border border-slate-700/40 rounded-xl p-6 max-w-sm mx-auto">
          <p className="text-slate-300 text-sm font-medium mb-4">Sign in to your account</p>
          <div className="space-y-3 mb-4">
            <div className="bg-slate-700/50 rounded-lg px-4 py-2.5 text-left"><span className="text-slate-300 text-sm">john.doe@example.com</span></div>
            <div className="bg-slate-700/50 rounded-lg px-4 py-2.5 text-left"><span className="text-slate-500 text-sm">••••••••••</span></div>
          </div>
          <button onClick={() => goToStep('dashboard')} className="w-full bg-[#3DE8D9] hover:bg-[#2fd4c6] text-slate-900 text-sm font-bold py-2.5 rounded-lg transition-colors" data-testid="demo-login-btn">Log In</button>
        </div>
        <div className="mt-6 bg-blue-500/10 border border-blue-500/20 rounded-lg px-4 py-2.5 inline-block">
          <p className="text-blue-400 text-xs italic">"From the RISEDUAL home page at risedual.ai, the user signs in to their account."</p>
        </div>
      </div>
    </div>
  </div>
);
