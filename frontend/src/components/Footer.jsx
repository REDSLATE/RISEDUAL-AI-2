import React from 'react';

const Footer = ({ onOpenLegal }) => {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-slate-600/30/60 bg-[#111C30]" data-testid="app-footer">
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
              <li><button onClick={() => onOpenLegal?.('terms')} className="text-slate-400 hover:text-slate-300 text-xs transition-colors" data-testid="footer-terms-link">Terms of Service</button></li>
              <li><button onClick={() => onOpenLegal?.('privacy')} className="text-slate-400 hover:text-slate-300 text-xs transition-colors" data-testid="footer-privacy-link">Privacy Policy</button></li>
              <li><button onClick={() => onOpenLegal?.('risk')} className="text-slate-400 hover:text-slate-300 text-xs transition-colors" data-testid="footer-risk-link">Risk Disclosure</button></li>
              <li><button onClick={() => onOpenLegal?.('disclaimer')} className="text-slate-400 hover:text-slate-300 text-xs transition-colors" data-testid="footer-disclaimer-link">Disclaimer</button></li>
              <li>
                <a
                  href="/compliance/oauth"
                  className="text-slate-400 hover:text-slate-300 text-xs transition-colors"
                  data-testid="footer-compliance-link"
                >
                  Compliance &amp; Security
                </a>
              </li>
            </ul>
          </div>
        </div>

        {/* Investment Risk Disclosure */}
        <div className="bg-slate-800/40 border border-slate-600/20 rounded-xl p-4 mb-6" data-testid="footer-risk-disclosure">
          <p className="text-amber-400 text-[10px] font-bold uppercase tracking-wider mb-2">Investment Risk Disclosure</p>
          <ul className="space-y-1.5 text-slate-400 text-[10px] leading-relaxed">
            <li><strong className="text-slate-300">High Risk Warning:</strong> Trading stocks, options, and digital assets involves significant risk of loss.</li>
            <li><strong className="text-slate-300">No Financial Advice:</strong> RISEDUAL AI is a <strong className="text-slate-300">financial research publishing platform</strong>. All content, including AI-generated signals and "4-Mind" insights, is for informational and educational purposes only.</li>
            <li><strong className="text-slate-300">Not a Broker/Adviser:</strong> RISEDUAL AI and RISEDUAL INC. are not registered investment advisers (RIAs) or broker-dealers. We do not provide personalized investment recommendations.</li>
            <li><strong className="text-slate-300">AI Limitations:</strong> Content is generated with assistance from RISEDUAL's four AI runtimes (Alpha 1.6, Camaro 1.3, Chevelle 1.3, RedEye 1.3). AI can "hallucinate" or provide inaccurate data. Users must perform their own due diligence before executing any trade.</li>
            <li><strong className="text-slate-300">Past Performance:</strong> Any displayed backtests or historical results are not indicative of future performance.</li>
          </ul>
        </div>

        {/* Divider + Bottom */}
        <div className="border-t border-slate-600/30/60 pt-5 flex flex-col sm:flex-row items-center justify-between gap-3">
          <p className="text-slate-400 text-[11px]">&copy; {year} RISEDUAL INC. All rights reserved. Not financial advice.</p>
          <p className="text-slate-700 text-[10px]">Powered by AI Multi-Model Consensus Engine</p>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
