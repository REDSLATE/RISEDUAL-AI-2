import React from 'react';
import { X, Zap, Shield, Brain, BarChart3, Globe, Users, Target, Rocket, Award } from 'lucide-react';
import { Card } from './ui/card';

const MISSION = {
  headline: 'Leveling the Playing Field',
  body: `RISEDUAL AI was founded on a simple belief: individual traders deserve the same intelligence that institutional desks have had for decades. By combining adversarial AI models, real-time macro scraping, and congressional-trade tracking, we give every trader a Wall-Street-grade edge — at a fraction of the cost.`,
};

const VALUES = [
  { icon: <Brain className="w-5 h-5" />, title: 'AI-First Thinking', desc: 'Every feature is designed around multi-model AI consensus — not gut feelings.' },
  { icon: <Shield className="w-5 h-5" />, title: 'Radical Transparency', desc: 'We show our prediction accuracy, data sources, and confidence levels. No black boxes.' },
  { icon: <Zap className="w-5 h-5" />, title: 'Speed Matters', desc: 'Real-time SSE streams, sub-second order flow heatmaps, and live macro intelligence.' },
  { icon: <Globe className="w-5 h-5" />, title: 'Global Macro Lens', desc: 'We scrape world events, foreign markets, congressional trades, and Fed announcements — so you never trade in a vacuum.' },
];

const CAPABILITIES = [
  { icon: <BarChart3 className="w-5 h-5 text-teal-400" />, title: 'Adversarial AI War Room', desc: 'Dual-model debate engine — Strategist generates signals, Auditor stress-tests them in real time.' },
  { icon: <Target className="w-5 h-5 text-orange-400" />, title: 'Market Prediction Engine', desc: 'Scrapes 8+ data sources (news, social, insider trades, crypto, real estate) and synthesizes AI-driven predictions.' },
  { icon: <Globe className="w-5 h-5 text-blue-400" />, title: 'Macro Intelligence', desc: 'Live world events, foreign market correlations, congressional stock trades, Fed announcements, and corporate lobbying data.' },
  { icon: <Zap className="w-5 h-5 text-lime-400" />, title: 'Live Order Flow Heatmaps', desc: 'Binance L2 depth visualization showing real-time buy/sell pressure across crypto pairs.' },
  { icon: <Shield className="w-5 h-5 text-violet-400" />, title: 'AI Strategy Builder & Backtester', desc: 'Build, backtest, and share trading strategies with a community marketplace.' },
  { icon: <Brain className="w-5 h-5 text-cyan-400" />, title: 'Portfolio-Aware AI Chat', desc: 'GPT-5.2 powered assistant that knows your paper portfolio and can execute trades with 2-step confirmation.' },
];

const STATS = [
  { value: '8+', label: 'Data Sources Scraped' },
  { value: '2X', label: 'AI Models Debating' },
  { value: '24/7', label: 'Real-Time Streams' },
  { value: '12K+', label: 'Lobbying Records' },
];

const AboutUs = ({ onClose, embedded = false }) => {
  const content = (
    <div className="space-y-16 sm:space-y-20" data-testid="about-us-content">

      {/* Mission */}
      <section className="text-center max-w-3xl mx-auto">
        <p className="text-[#3DE8D9] text-sm font-semibold tracking-widest uppercase mb-3">Our Mission</p>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-4">{MISSION.headline}</h2>
        <p className="text-slate-300 text-base leading-relaxed">{MISSION.body}</p>
      </section>

      {/* Stats Bar */}
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {STATS.map(s => (
          <Card key={s.label} className="bg-slate-700/60 border-slate-400/30 rounded-xl p-5 text-center">
            <p className="text-2xl sm:text-3xl font-black text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">{s.value}</p>
            <p className="text-slate-400 text-xs mt-1">{s.label}</p>
          </Card>
        ))}
      </section>

      {/* Values */}
      <section>
        <p className="text-[#3DE8D9] text-sm font-semibold tracking-widest uppercase mb-3 text-center">What Drives Us</p>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-8 text-center">Our Core Values</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {VALUES.map(v => (
            <Card key={v.title} className="bg-slate-700/60 border-slate-400/30 rounded-xl p-5 flex gap-4">
              <div className="w-10 h-10 rounded-lg bg-[#3DE8D9]/10 flex items-center justify-center text-[#3DE8D9] shrink-0">
                {v.icon}
              </div>
              <div>
                <h3 className="text-white font-semibold text-sm">{v.title}</h3>
                <p className="text-slate-400 text-xs mt-1 leading-relaxed">{v.desc}</p>
              </div>
            </Card>
          ))}
        </div>
      </section>

      {/* Platform Capabilities */}
      <section>
        <p className="text-[#3DE8D9] text-sm font-semibold tracking-widest uppercase mb-3 text-center">The Platform</p>
        <h2 className="text-2xl sm:text-3xl font-bold text-white mb-8 text-center">What RISEDUAL AI Does</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {CAPABILITIES.map(c => (
            <Card key={c.title} className="bg-slate-700/60 border-slate-400/30 rounded-xl p-5">
              <div className="w-9 h-9 rounded-lg bg-slate-800/60 flex items-center justify-center mb-3">
                {c.icon}
              </div>
              <h3 className="text-white font-semibold text-sm mb-1">{c.title}</h3>
              <p className="text-slate-400 text-xs leading-relaxed">{c.desc}</p>
            </Card>
          ))}
        </div>
      </section>

      {/* CTA */}
      {!embedded && (
        <section className="text-center py-8">
          <h2 className="text-xl sm:text-2xl font-bold text-white mb-3">Ready to trade smarter?</h2>
          <p className="text-slate-400 text-sm mb-6">Join thousands of traders using AI-powered intelligence.</p>
          <button
            onClick={onClose}
            className="px-8 py-3 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold hover:opacity-90 transition-opacity"
            data-testid="about-cta-btn"
          >
            Back to Dashboard
          </button>
        </section>
      )}
    </div>
  );

  // Embedded mode (for Landing Page)
  if (embedded) {
    return (
      <section id="about-us" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto" data-testid="about-us-section">
        {content}
      </section>
    );
  }

  // Full-screen overlay mode (for logged-in users)
  return (
    <div className="fixed inset-0 z-[60] bg-[#060E1F] overflow-y-auto" data-testid="about-us-overlay">
      <div className="sticky top-0 z-10 bg-[#060E1F]/90 backdrop-blur-xl border-b border-slate-700/40 px-4 sm:px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Rocket className="w-5 h-5 text-[#3DE8D9]" />
          <h1 className="text-white font-bold text-lg tracking-tight">
            About <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">RISEDUAL AI</span>
          </h1>
        </div>
        <button
          onClick={onClose}
          className="w-8 h-8 rounded-lg bg-slate-700/60 hover:bg-slate-600/60 flex items-center justify-center text-slate-400 hover:text-white transition-colors"
          data-testid="about-close-btn"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-10 sm:py-16">
        {content}
      </div>
    </div>
  );
};

export default AboutUs;
