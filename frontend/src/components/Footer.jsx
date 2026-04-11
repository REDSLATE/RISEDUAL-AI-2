import React from 'react';

const Footer = () => {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-slate-600/30/60 bg-[#0B1120]" data-testid="app-footer">
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8 sm:py-10">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 sm:gap-8 mb-8">
          {/* Brand */}
          <div className="col-span-2 sm:col-span-1">
            <div className="flex items-center gap-2 mb-3">
              <img src="/logo-icon.png" alt="RISEDUAL AI" className="w-7 h-7 object-contain brightness-125" />
              <span className="text-white font-bold text-lg tracking-wide" style={{ fontFamily: 'Manrope, sans-serif' }}>RISEDUAL <span className="text-[#3DE8D9]">AI</span></span>
            </div>
            <p className="text-slate-300 text-xs leading-relaxed max-w-[220px]">
              AI-powered trading intelligence. Real-time market data, multi-model analysis, and actionable signals.
            </p>
          </div>

          {/* Platform */}
          <div>
            <h4 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3">Platform</h4>
            <ul className="space-y-2">
              {[
                { label: 'AI War Room', id: 'ai-war-room' },
                { label: 'Market Predictions', id: 'market-prediction' },
                { label: 'Options Radar', id: 'options-radar' },
                { label: 'Sector Heatmap', id: 'sector-heatmap' },
              ].map(l => (
                <li key={l.id}>
                  <button onClick={() => document.getElementById(l.id)?.scrollIntoView({ behavior: 'smooth' })}
                    className="text-slate-400 hover:text-slate-300 text-xs transition-colors">
                    {l.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Resources */}
          <div>
            <h4 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3">Resources</h4>
            <ul className="space-y-2">
              {[
                { label: 'Company Research', id: 'company-research' },
                { label: 'Dark Pool Data', id: 'dark-pool' },
                { label: 'Macro Intelligence', id: 'macro-dashboard' },
                { label: 'Crypto Market', id: 'crypto' },
              ].map(l => (
                <li key={l.id}>
                  <button onClick={() => document.getElementById(l.id)?.scrollIntoView({ behavior: 'smooth' })}
                    className="text-slate-400 hover:text-slate-300 text-xs transition-colors">
                    {l.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Legal */}
          <div>
            <h4 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3">Legal</h4>
            <ul className="space-y-2">
              <li><span className="text-slate-300 text-xs">Terms of Service</span></li>
              <li><span className="text-slate-300 text-xs">Privacy Policy</span></li>
              <li><span className="text-slate-300 text-xs">Risk Disclosure</span></li>
            </ul>
          </div>
        </div>

        {/* Divider + Bottom */}
        <div className="border-t border-slate-600/30/60 pt-5 flex flex-col sm:flex-row items-center justify-between gap-3">
          <p className="text-slate-400 text-[11px]">&copy; {year} RISEDUAL AI. All rights reserved. Not financial advice.</p>
          <p className="text-slate-700 text-[10px]">Powered by AI Multi-Model Consensus Engine</p>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
