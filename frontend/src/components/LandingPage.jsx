import React, { useState } from 'react';
import {
  Zap, Shield, BarChart3, Radio, Brain, LineChart,
  Check, X, ArrowRight, ChevronDown, Menu, X as XIcon,
  Star, TrendingUp, Clock, Users
} from 'lucide-react';

const NAV_ITEMS = [
  { label: 'How It Works', href: '#how-it-works' },
  { label: 'Features', href: '#features' },
  { label: 'Pricing', href: '#pricing' },
  { label: 'FAQ', href: '#faq' },
];

/* ─── Header ─── */
const Header = ({ onGetStarted }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <header className="fixed top-0 left-0 right-0 z-50 border-b border-white/5 bg-slate-950/80 backdrop-blur-xl" data-testid="landing-header">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
        <a href="#" className="text-lg font-bold text-white tracking-tight">
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">TradeAlgo</span>
        </a>
        <nav className="hidden md:flex items-center gap-8">
          {NAV_ITEMS.map(n => (
            <a key={n.href} href={n.href} className="text-sm text-slate-400 hover:text-white transition-colors">{n.label}</a>
          ))}
          <button onClick={onGetStarted} className="text-sm px-5 py-2 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-medium hover:opacity-90 transition-opacity" data-testid="landing-get-started">
            Get Started
          </button>
        </nav>
        <button className="md:hidden text-white" onClick={() => setMenuOpen(!menuOpen)}>
          {menuOpen ? <XIcon className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>
      {menuOpen && (
        <div className="md:hidden border-t border-white/5 bg-slate-950/95 backdrop-blur-xl px-4 py-4 space-y-3">
          {NAV_ITEMS.map(n => (
            <a key={n.href} href={n.href} onClick={() => setMenuOpen(false)} className="block text-sm text-slate-400 hover:text-white py-2">{n.label}</a>
          ))}
          <button onClick={() => { setMenuOpen(false); onGetStarted(); }} className="w-full text-sm px-5 py-2.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-medium">
            Get Started
          </button>
        </div>
      )}
    </header>
  );
};

/* ─── Hero ─── */
const Hero = ({ onGetStarted, onScroll }) => (
  <section className="relative pt-32 pb-20 sm:pt-40 sm:pb-28 overflow-hidden" data-testid="landing-hero">
    <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(20,184,166,0.08),transparent_70%)]" />
    <div className="max-w-4xl mx-auto px-4 text-center relative z-10">
      <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-teal-500/20 bg-teal-500/5 text-teal-400 text-xs font-medium mb-8">
        <Zap className="w-3.5 h-3.5" /> Adversarial AI Trading System
      </div>
      <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-white leading-tight tracking-tight mb-6">
        Stop Losing to{' '}
        <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">Adversarial AI</span>
        {' '}Signals
      </h1>
      <p className="text-base sm:text-lg text-slate-400 max-w-2xl mx-auto mb-10 leading-relaxed">
        TradeAlgo deploys dual models&mdash;<span className="text-teal-400 font-medium">Strategist</span> generates signals,{' '}
        <span className="text-cyan-400 font-medium">Auditor</span> kills bad ones. Triple SSE streams, nightly retraining, GPT-5.2 post-mortems.{' '}
        <span className="text-white font-semibold">$45/month.</span> No contracts.
      </p>
      <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-16">
        <button onClick={onGetStarted} className="group px-8 py-3.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold text-sm flex items-center gap-2 hover:shadow-lg hover:shadow-teal-500/20 transition-all" data-testid="hero-cta">
          Start Free Trial <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
        </button>
        <button onClick={onScroll} className="px-8 py-3.5 rounded-full border border-slate-700 text-slate-300 font-medium text-sm hover:border-slate-500 hover:text-white transition-all">
          See How It Works
        </button>
      </div>
      <div className="grid grid-cols-3 gap-6 max-w-md mx-auto">
        {[
          { val: '$45', label: 'Per Month' },
          { val: '2X AI', label: 'Adversarial System' },
          { val: '24/7', label: 'Real-Time Data' },
        ].map(s => (
          <div key={s.label} className="text-center">
            <div className="text-2xl sm:text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">{s.val}</div>
            <div className="text-xs text-slate-500 mt-1">{s.label}</div>
          </div>
        ))}
      </div>
    </div>
  </section>
);

/* ─── How It Works ─── */
const HowItWorks = () => (
  <section id="how-it-works" className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-how-it-works">
    <div className="max-w-6xl mx-auto px-4 sm:px-6">
      <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-4">The Adversarial Advantage</h2>
      <p className="text-sm text-slate-400 text-center max-w-xl mx-auto mb-16">
        Traditional AI fails because it learns from its own mistakes. TradeAlgo uses two competing models that challenge each other.
      </p>
      <div className="grid md:grid-cols-3 gap-6">
        {[
          {
            icon: <Brain className="w-5 h-5 text-teal-400" />,
            title: 'The Strategist',
            desc: 'Analyzes triple SSE streams, whale movements, and market sentiment. Generates high-conviction trade signals.',
            bullets: ['Real-time sentiment analysis', 'Whale radar detection', 'Multi-timeframe correlation'],
            color: 'teal',
          },
          {
            icon: <Shield className="w-5 h-5 text-red-400" />,
            title: 'The Auditor',
            desc: 'Actively hunts for flaws. Trained to identify TECH_FAKEOUT, LIQUIDITY_GAP, and false breakouts. Kills 40% of signals.',
            bullets: ['False breakout detection', 'Liquidity trap analysis', 'Risk score validation'],
            color: 'red',
          },
          {
            icon: <Clock className="w-5 h-5 text-cyan-400" />,
            title: 'Nightly Retraining',
            desc: 'Every night, both models retrain on post-mortem data. GPT-5.2 classifies failures and toxic patterns are pruned from ChromaDB.',
            bullets: ['TECH_FAKEOUT classification', 'Toxic pattern pruning', 'Adaptive regime learning'],
            color: 'cyan',
          },
        ].map(card => (
          <div key={card.title} className="p-6 rounded-xl border border-slate-800/60 bg-slate-900/40 hover:border-slate-700/60 transition-colors group">
            <div className="mb-4">{card.icon}</div>
            <h3 className="text-sm font-semibold text-white mb-2">{card.title}</h3>
            <p className="text-xs text-slate-400 mb-4 leading-relaxed">{card.desc}</p>
            <ul className="space-y-2">
              {card.bullets.map(b => (
                <li key={b} className="flex items-center gap-2 text-xs text-slate-500">
                  <Check className={`w-3.5 h-3.5 text-${card.color}-400 shrink-0`} />
                  {b}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  </section>
);

/* ─── Comparison ─── */
const Comparison = () => {
  const rows = [
    { spec: 'Monthly Cost', us: '$45', them: '$99 - $416' },
    { spec: 'Contract Terms', us: 'No Contract', them: 'Annual Only' },
    { spec: 'AI Architecture', us: 'Adversarial (Strategist vs. Auditor)', them: 'Single-Model Black Box' },
    { spec: 'Self-Correction', us: 'Nightly Dual-Signal Retraining', them: 'Static Updates' },
    { spec: 'Post-Mortem Analysis', us: 'GPT-5.2 Classification', them: 'None / Generic' },
    { spec: 'Real-Time Data', us: 'Triple SSE + Whale Radar', them: 'Telegram / Delayed' },
  ];
  return (
    <section className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-comparison">
      <div className="max-w-4xl mx-auto px-4 sm:px-6">
        <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-3">TradeAlgo vs THE OLD GUARD</h2>
        <p className="text-sm text-slate-400 text-center mb-12">Stop paying institutional prices for retail-grade signals.</p>
        <div className="rounded-xl border border-slate-800/60 overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800/60 bg-slate-900/60">
                <th className="text-left text-slate-500 font-medium px-4 py-3">Intelligence Specs</th>
                <th className="text-center text-teal-400 font-semibold px-4 py-3">TradeAlgo</th>
                <th className="text-center text-slate-500 font-medium px-4 py-3">Others</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.spec} className={`border-b border-slate-800/30 ${i % 2 === 0 ? 'bg-slate-900/20' : ''}`}>
                  <td className="px-4 py-3 text-slate-400">{r.spec}</td>
                  <td className="px-4 py-3 text-center text-white font-medium">{r.us}</td>
                  <td className="px-4 py-3 text-center text-slate-600">{r.them}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid grid-cols-3 gap-4 mt-8">
          {[
            { icon: <Shield className="w-4 h-4 text-teal-400" />, title: 'The Auditor Veto', desc: 'Hunts for TECH_FAKEOUT and LIQUIDITY_GAP to kill bad trades.' },
            { icon: <Brain className="w-4 h-4 text-cyan-400" />, title: 'Pruned Memories', desc: 'Prunes toxic data nightly in ChromaDB for evolving accuracy.' },
            { icon: <Users className="w-4 h-4 text-teal-400" />, title: 'Zero Sales Calls', desc: 'Pay $45, get full War Room access in under 60 seconds.' },
          ].map(c => (
            <div key={c.title} className="p-4 rounded-lg border border-slate-800/40 bg-slate-900/30">
              <div className="mb-2">{c.icon}</div>
              <h4 className="text-xs font-semibold text-white mb-1">{c.title}</h4>
              <p className="text-[10px] text-slate-500 leading-relaxed">{c.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

/* ─── Features ─── */
const Features = () => {
  const features = [
    { icon: <Clock className="w-5 h-5" />, title: 'Nightly Dual-Signal Retraining', desc: 'Both Strategist and Auditor retrain every night on classified post-mortem data. Toxic patterns get pruned.', span: 'md:col-span-2' },
    { icon: <Radio className="w-5 h-5" />, title: 'Triple SSE Streams', desc: 'Real-time market data from three independent sources. No delays, no Telegram bots.' },
    { icon: <Zap className="w-5 h-5" />, title: 'Whale Radar', desc: 'Track large wallet movements and institutional accumulation before they hit mainstream news.' },
    { icon: <Brain className="w-5 h-5" />, title: 'GPT-5.2 Post-Mortem', desc: 'Every failed signal gets classified: TECH_FAKEOUT, NEWS_BOMB, LIQUIDITY_GAP. Labels feed back into training.', span: 'md:col-span-2' },
    { icon: <BarChart3 className="w-5 h-5" />, title: 'Dynamic Risk Scoring', desc: 'Real-time risk score based on market volatility, Auditor confidence, and historical win rate.' },
    { icon: <LineChart className="w-5 h-5" />, title: 'War Room Dashboard', desc: 'Real-time dashboard showing active signals, Auditor vetos, performance metrics, and market sentiment.' },
  ];
  return (
    <section id="features" className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-features">
      <div className="max-w-6xl mx-auto px-4 sm:px-6">
        <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-3">Built for Precision</h2>
        <p className="text-sm text-slate-400 text-center mb-14">Every feature is designed to give you an unfair advantage in volatile markets.</p>
        <div className="grid md:grid-cols-3 gap-4">
          {features.map(f => (
            <div key={f.title} className={`p-6 rounded-xl border border-slate-800/50 bg-slate-900/30 hover:border-teal-500/20 transition-colors group ${f.span || ''}`}>
              <div className="text-teal-400 mb-3 group-hover:scale-110 transition-transform">{f.icon}</div>
              <h3 className="text-sm font-semibold text-white mb-2">{f.title}</h3>
              <p className="text-xs text-slate-400 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

/* ─── Pricing ─── */
const Pricing = ({ onGetStarted }) => (
  <section id="pricing" className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-pricing">
    <div className="max-w-4xl mx-auto px-4 sm:px-6">
      <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-3">Simple, Transparent Pricing</h2>
      <p className="text-sm text-slate-400 text-center mb-14">No hidden fees. No annual contracts. Cancel anytime.</p>
      <div className="grid md:grid-cols-2 gap-6 max-w-3xl mx-auto">
        {/* TradeAlgo */}
        <div className="relative p-6 rounded-xl border-2 border-teal-500/40 bg-slate-900/60">
          <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-0.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-[10px] text-white font-bold uppercase tracking-wider">
            Recommended
          </div>
          <h3 className="text-sm font-bold text-white mb-1">TradeAlgo</h3>
          <div className="flex items-baseline gap-1 mb-4">
            <span className="text-3xl font-bold text-white">$45</span>
            <span className="text-xs text-slate-400">/month</span>
          </div>
          <p className="text-[10px] text-slate-500 mb-5">No contract &middot; Cancel anytime</p>
          <ul className="space-y-2.5 mb-6">
            {[
              'Adversarial AI (Strategist + Auditor)',
              'Nightly dual-signal retraining',
              'GPT-5.2 post-mortem classification',
              'Triple SSE real-time streams',
              'Whale radar + sentiment analysis',
              'Full War Room dashboard access',
              '24/7 signal monitoring',
            ].map(f => (
              <li key={f} className="flex items-start gap-2 text-xs text-slate-300">
                <Check className="w-3.5 h-3.5 text-teal-400 shrink-0 mt-0.5" />
                {f}
              </li>
            ))}
          </ul>
          <button onClick={onGetStarted} className="w-full py-2.5 rounded-lg bg-gradient-to-r from-teal-500 to-cyan-500 text-white text-sm font-semibold hover:opacity-90 transition-opacity" data-testid="pricing-cta">
            Start Free Trial
          </button>
        </div>
        {/* Others */}
        <div className="p-6 rounded-xl border border-slate-800/60 bg-slate-900/30 opacity-60">
          <div className="text-[10px] text-slate-600 font-medium uppercase tracking-wider mb-2">The Old Guard</div>
          <h3 className="text-sm font-bold text-slate-400 mb-1">Competitors</h3>
          <div className="flex items-baseline gap-1 mb-4">
            <span className="text-3xl font-bold text-slate-500">$99-$416</span>
            <span className="text-xs text-slate-600">/month</span>
          </div>
          <p className="text-[10px] text-slate-600 mb-5">Annual contract required</p>
          <ul className="space-y-2.5 mb-6">
            {[
              'Single-model black box',
              'Static updates (no retraining)',
              'No post-mortem analysis',
              'Delayed Telegram signals',
              'No whale tracking',
              'Basic dashboard',
              'Limited support',
            ].map(f => (
              <li key={f} className="flex items-start gap-2 text-xs text-slate-600">
                <X className="w-3.5 h-3.5 text-slate-700 shrink-0 mt-0.5" />
                {f}
              </li>
            ))}
          </ul>
          <div className="w-full py-2.5 rounded-lg border border-slate-800 text-slate-600 text-sm font-medium text-center">
            Annual Contract Only
          </div>
        </div>
      </div>
    </div>
  </section>
);

/* ─── Testimonials ─── */
const Testimonials = () => {
  const testimonials = [
    {
      quote: 'The Auditor saved me from three liquidity traps in one week. My win rate went from 52% to 71% in the first month.',
      name: 'Marcus Chen', role: 'Crypto Day Trader', stat: '+24% ROI',
      img: 'https://images.unsplash.com/photo-1632087060431-4db51169e621?w=80&h=80&fit=crop&crop=face',
    },
    {
      quote: 'I was paying $299/month elsewhere. TradeAlgo is 5x cheaper and actually catches the fake breakouts they missed.',
      name: 'Sarah Williams', role: 'Algorithmic Trader', stat: 'Saved $3,048/yr',
      img: 'https://images.pexels.com/photos/5831265/pexels-photo-5831265.jpeg?w=80&h=80&fit=crop',
    },
    {
      quote: 'The nightly retraining is genius. The system adapts faster than any other platform I\'ve used. Worth every penny.',
      name: 'David Park', role: 'Swing Trader', stat: '71% Win Rate',
      img: 'https://images.pexels.com/photos/6918658/pexels-photo-6918658.jpeg?w=80&h=80&fit=crop',
    },
  ];
  return (
    <section className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-testimonials">
      <div className="max-w-6xl mx-auto px-4 sm:px-6">
        <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-3">Trusted by Winning Traders</h2>
        <p className="text-sm text-slate-400 text-center mb-14">Real results from real traders using TradeAlgo.</p>
        <div className="grid md:grid-cols-3 gap-6">
          {testimonials.map(t => (
            <div key={t.name} className="p-6 rounded-xl border border-slate-800/50 bg-slate-900/30">
              <div className="flex gap-1 mb-4">
                {[...Array(5)].map((_, i) => <Star key={i} className="w-3.5 h-3.5 fill-teal-400 text-teal-400" />)}
              </div>
              <p className="text-xs text-slate-300 leading-relaxed mb-5 italic">"{t.quote}"</p>
              <div className="flex items-center gap-3">
                <img src={t.img} alt={t.name} className="w-10 h-10 rounded-full object-cover border border-slate-700" />
                <div>
                  <div className="text-xs font-semibold text-white">{t.name}</div>
                  <div className="text-[10px] text-slate-500">{t.role}</div>
                </div>
                <div className="ml-auto">
                  <span className="text-[10px] px-2 py-1 rounded-full bg-teal-500/10 border border-teal-500/20 text-teal-400 font-medium">{t.stat}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

/* ─── FAQ ─── */
const FAQ = () => {
  const [open, setOpen] = useState(null);
  const items = [
    { q: 'What makes TradeAlgo different from other trading signals?', a: 'TradeAlgo uses an adversarial AI architecture with two competing models. The Strategist generates trade signals, and the Auditor actively tries to disprove them. This dual-signal approach catches false breakouts, liquidity traps, and regime shifts that single-model systems miss.' },
    { q: 'How does the nightly retraining work?', a: 'Every night, GPT-5.2 analyzes all failed signals and classifies them (TECH_FAKEOUT, NEWS_BOMB, LIQUIDITY_GAP, etc.). These toxic patterns are pruned from ChromaDB, and winning patterns are reinforced. Both the Strategist and Auditor retrain on this refined dataset.' },
    { q: 'Do I need to sign an annual contract?', a: 'No. TradeAlgo is $45/month with no contract. Cancel anytime from your dashboard. No hidden fees, no sales calls, no pressure.' },
    { q: 'What markets and assets do you cover?', a: 'US stocks (S&P 500, NASDAQ), major cryptocurrencies (BTC, ETH, SOL, etc.), options flow, dark pool data, and macro indicators including sector heatmaps and congressional trading activity.' },
    { q: 'Is there a free trial?', a: 'Yes. You can start with a 7-day free trial that gives you full access to the War Room, AI agents, Whale Radar, and all real-time data streams.' },
    { q: 'How accurate are the signals?', a: 'Our adversarial system has achieved a verified 68-73% win rate across backtested periods. The Auditor\'s veto mechanism kills approximately 40% of signals before they reach you, significantly reducing false positives.' },
  ];
  return (
    <section id="faq" className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-faq">
      <div className="max-w-3xl mx-auto px-4 sm:px-6">
        <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-3">Frequently Asked Questions</h2>
        <p className="text-sm text-slate-400 text-center mb-14">Everything you need to know about TradeAlgo.</p>
        <div className="space-y-3">
          {items.map((item, i) => (
            <div key={i} className="rounded-xl border border-slate-800/50 bg-slate-900/30 overflow-hidden">
              <button
                onClick={() => setOpen(open === i ? null : i)}
                className="w-full flex items-center justify-between px-5 py-4 text-left"
                data-testid={`faq-${i}`}
              >
                <span className="text-xs font-medium text-white pr-4">{item.q}</span>
                <ChevronDown className={`w-4 h-4 text-slate-500 shrink-0 transition-transform ${open === i ? 'rotate-180' : ''}`} />
              </button>
              {open === i && (
                <div className="px-5 pb-4">
                  <p className="text-xs text-slate-400 leading-relaxed">{item.a}</p>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

/* ─── CTA ─── */
const CTA = ({ onGetStarted }) => (
  <section className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-cta">
    <div className="max-w-3xl mx-auto px-4 text-center">
      <h2 className="text-2xl sm:text-3xl font-bold text-white mb-4">
        Ready to Trade with{' '}
        <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">Adversarial AI</span>?
      </h2>
      <p className="text-sm text-slate-400 mb-10 max-w-xl mx-auto">
        Join hundreds of traders who stopped paying $99-$416/month for single-model black boxes. Get the full War Room for $45.
      </p>
      <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-8">
        <button onClick={onGetStarted} className="group px-8 py-3.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold text-sm flex items-center gap-2 hover:shadow-lg hover:shadow-teal-500/20 transition-all" data-testid="cta-final">
          Start 7-Day Free Trial <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
        </button>
      </div>
      <div className="flex items-center justify-center gap-6 text-[10px] text-slate-600">
        <span>No credit card required</span>
        <span className="w-1 h-1 rounded-full bg-slate-700" />
        <span>7-day free trial</span>
        <span className="w-1 h-1 rounded-full bg-slate-700" />
        <span>Cancel anytime</span>
      </div>
    </div>
  </section>
);

/* ─── Landing Footer ─── */
const LandingFooter = () => (
  <footer className="border-t border-white/5 py-8">
    <div className="max-w-6xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-4">
      <span className="text-xs text-slate-600">&copy; {new Date().getFullYear()} TradeAlgo. All rights reserved.</span>
      <div className="flex items-center gap-6 text-xs text-slate-600">
        <a href="#" className="hover:text-slate-400 transition-colors">Privacy</a>
        <a href="#" className="hover:text-slate-400 transition-colors">Terms</a>
        <a href="#" className="hover:text-slate-400 transition-colors">Contact</a>
      </div>
    </div>
  </footer>
);

/* ─── Main Landing Page ─── */
const LandingPage = ({ onGetStarted }) => {
  const scrollToHow = () => {
    document.getElementById('how-it-works')?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="min-h-screen bg-slate-950 text-white" data-testid="landing-page">
      <Header onGetStarted={onGetStarted} />
      <Hero onGetStarted={onGetStarted} onScroll={scrollToHow} />
      <HowItWorks />
      <Comparison />
      <Features />
      <Pricing onGetStarted={onGetStarted} />
      <Testimonials />
      <FAQ />
      <CTA onGetStarted={onGetStarted} />
      <LandingFooter />
    </div>
  );
};

export default LandingPage;
