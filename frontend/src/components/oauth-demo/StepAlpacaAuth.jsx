import React from 'react';
import { CheckCircle, Lock, ArrowRight } from 'lucide-react';
import { NarrationGuide } from './DemoShared';

export const StepAlpacaAuth = ({ handleAuthorize, goToStep }) => (
  <div className="max-w-md w-full">
    <div className="bg-white rounded-2xl overflow-hidden shadow-2xl">
      <div className="bg-[#1A1A2E] px-6 py-5 flex items-center justify-center gap-3">
        <div className="w-8 h-8 rounded-full bg-[#F7D046] flex items-center justify-center"><span className="text-[#1A1A2E] font-bold text-sm">A</span></div>
        <span className="text-white font-bold text-lg">Alpaca</span>
      </div>
      <div className="p-6 space-y-5">
        <div className="text-center">
          <h2 className="text-gray-900 text-lg font-bold mb-1">Authorize RISEDUAL AI</h2>
          <p className="text-gray-500 text-sm">risedual.ai wants to access your Alpaca account</p>
        </div>
        <div className="bg-gray-50 rounded-xl p-4 space-y-3">
          <p className="text-gray-700 text-xs font-semibold uppercase tracking-wider">This app will be able to:</p>
          <div className="space-y-2.5">
            {[
              { title: 'View your account information', sub: 'Portfolio value, positions, and buying power' },
              { title: 'Place and manage trades', sub: 'Submit, modify, and cancel orders on your behalf' },
              { title: 'Access market data', sub: 'Real-time quotes, historical data, and news' },
            ].map(p => (
              <div key={p.title} className="flex items-start gap-2">
                <CheckCircle className="w-4 h-4 text-green-500 mt-0.5 shrink-0" />
                <div>
                  <p className="text-gray-800 text-sm font-medium">{p.title}</p>
                  <p className="text-gray-500 text-xs">{p.sub}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-500 bg-blue-50 rounded-lg p-3">
          <Lock className="w-3.5 h-3.5 text-blue-500 shrink-0" />
          <span>RISEDUAL AI will <strong>not</strong> have access to your password or the ability to withdraw funds.</span>
        </div>
        <div className="flex gap-3">
          <button onClick={() => goToStep('start')} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-gray-600 text-sm font-medium hover:bg-gray-50 transition-colors">Deny</button>
          <button onClick={handleAuthorize} className="flex-1 py-2.5 rounded-lg bg-[#F7D046] text-[#1A1A2E] text-sm font-bold hover:bg-[#F7D046]/90 transition-colors flex items-center justify-center gap-2" data-testid="demo-authorize-btn">
            Authorize <ArrowRight className="w-4 h-4" />
          </button>
        </div>
        <p className="text-center text-gray-400 text-[10px]">By authorizing, you agree to Alpaca's Terms of Service</p>
      </div>
    </div>
    <NarrationGuide text={'"When the user clicks Connect Alpaca, RISEDUAL redirects them to Alpaca\'s secure OAuth page to complete the connection."'} />
  </div>
);
