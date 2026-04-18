import React from 'react';
import { Shield, Lock, CheckCircle } from 'lucide-react';

export const StepDisclosure = ({ goToStep }) => (
  <div className="max-w-xl w-full">
    <div className="bg-slate-900 rounded-2xl border border-slate-700/50 overflow-hidden">
      <div className="bg-slate-800/60 px-6 py-4 border-b border-slate-700/50 flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-[#3DE8D9]/20 flex items-center justify-center"><span className="text-[#3DE8D9] font-bold text-sm">R</span></div>
        <span className="text-white font-bold">RISEDUAL AI</span>
        <span className="text-slate-500 text-xs ml-auto">Authorization Disclosure</span>
      </div>
      <div className="p-6 space-y-5">
        <div className="bg-amber-50 border-l-4 border-red-600 rounded-r-lg p-5">
          <h3 className="text-red-700 font-bold text-lg mb-3">Authorize RISEDUAL AI</h3>
          <p className="text-red-700 font-bold text-sm leading-relaxed mb-4">
            By allowing RISEDUAL AI to access your Alpaca account, you are granting RISEDUAL AI access to your account information and authorization to place transactions at your direction.
          </p>
          <p className="text-red-700 font-bold text-sm leading-relaxed mb-5">
            Alpaca does not warrant or guarantee that RISEDUAL AI will work as advertised or expected. Before authorizing, learn more about{' '}
            <a href="https://risedual.ai" className="underline">RISEDUAL AI</a>.
          </p>
          <div className="flex gap-6 justify-center">
            <button onClick={() => goToStep('start')} className="text-gray-700 font-bold text-sm uppercase tracking-wide hover:text-gray-900 transition-colors px-6 py-2">DENY</button>
            <button onClick={() => goToStep('alpaca_auth')} className="text-red-700 font-bold text-sm uppercase tracking-wide hover:text-red-900 transition-colors px-6 py-2" data-testid="demo-disclosure-allow">ALLOW</button>
          </div>
        </div>
        <div className="bg-slate-800/40 rounded-lg p-3">
          <p className="text-slate-400 text-xs italic">*Acknowledgement of the disclosure must be done prior to a client connecting their Alpaca account.</p>
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 text-xs text-slate-400"><Shield className="w-3 h-3 text-[#3DE8D9] shrink-0" /><span>RISEDUAL AI uses OAuth2 — we never access your Alpaca password</span></div>
          <div className="flex items-center gap-2 text-xs text-slate-400"><Lock className="w-3 h-3 text-[#3DE8D9] shrink-0" /><span>All credentials encrypted with AES-256 in our secure KeyVault</span></div>
          <div className="flex items-center gap-2 text-xs text-slate-400"><CheckCircle className="w-3 h-3 text-[#3DE8D9] shrink-0" /><span>You can revoke access at any time from your Alpaca dashboard</span></div>
        </div>
      </div>
    </div>
  </div>
);
