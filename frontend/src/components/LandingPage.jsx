import React, { useState, useEffect } from 'react';
import {
  Zap, Shield, BarChart3, Radio, Brain, LineChart,
  Check, X, ArrowRight, ChevronDown, Menu, X as XIcon,
  TrendingUp, Clock, Users, Play
} from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const NAV_ITEMS = [
  { label: 'How It Works', href: '#how-it-works' },
  { label: 'Features', href: '#features' },
  { label: 'About Us', href: '#about-us' },
  { label: 'Pricing', href: '#pricing' },
  { label: 'FAQ', href: '#faq' },
];

/* ─── Header ─── */
const Header = ({ onGetStarted, onLogin, onTryDemo }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <header className="fixed top-0 left-0 right-0 z-50 border-b border-white/5 bg-slate-950/80 backdrop-blur-xl" data-testid="landing-header">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
        <a href="#" className="text-lg font-bold text-white tracking-tight">
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">RISEDUAL</span>
          <span className="text-slate-300 text-sm ml-1">AI</span>
        </a>
        <nav className="hidden md:flex items-center gap-8">
          {NAV_ITEMS.map(n => (
            <a key={n.href} href={n.href} className="text-sm text-slate-400 hover:text-white transition-colors">{n.label}</a>
          ))}
          <button onClick={onTryDemo} className="text-sm text-teal-400 hover:text-teal-300 transition-colors font-medium" data-testid="landing-try-demo">
            Try Demo
          </button>
          <button onClick={onLogin} className="text-sm text-slate-300 hover:text-white transition-colors" data-testid="landing-login-btn">
            Log In
          </button>
          <button onClick={onGetStarted} className="text-sm px-5 py-2 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-medium hover:opacity-90 transition-opacity" data-testid="landing-get-started">
            Join Waitlist
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
          <button onClick={() => { setMenuOpen(false); onTryDemo(); }} className="w-full text-sm px-5 py-2.5 rounded-full border border-teal-500/40 text-teal-400 font-medium" data-testid="mobile-try-demo">
            Try Live Demo
          </button>
          <button onClick={() => { setMenuOpen(false); onLogin(); }} className="w-full text-sm px-5 py-2.5 rounded-full border border-slate-600 text-slate-300 hover:text-white font-medium" data-testid="mobile-login-btn">
            Log In
          </button>
          <button onClick={() => { setMenuOpen(false); onGetStarted(); }} className="w-full text-sm px-5 py-2.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-medium">
            Join Waitlist
          </button>
        </div>
      )}
    </header>
  );
};

/* ─── Hero ─── */
const Hero = ({ onGetStarted, onScroll, onTryDemo }) => (
  <section className="relative pt-32 pb-20 sm:pt-40 sm:pb-28 overflow-hidden" data-testid="landing-hero">
    <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(20,184,166,0.08),transparent_70%)]" />
    <div className="max-w-4xl mx-auto px-4 text-center relative z-10">
      <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-teal-500/20 bg-teal-500/5 text-teal-400 text-xs font-medium mb-8">
        <Zap className="w-3.5 h-3.5" /> Adversarial AI Trading System
      </div>
      <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-white leading-tight tracking-tight mb-6">
        Our AI Predicted{' '}
        <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">62% of Market Moves</span>
        {' '}Last Quarter
      </h1>
      <p className="text-base sm:text-lg text-slate-400 max-w-2xl mx-auto mb-10 leading-relaxed">
        RISEDUAL AI deploys dual models&mdash;<span className="text-teal-400 font-medium">Strategist</span> generates signals,{' '}
        <span className="text-cyan-400 font-medium">Auditor</span> kills bad ones. Sharpe 1.56, 11.2% max drawdown, autonomous paper trading live.{' '}
        <span className="text-white font-semibold">$55/month.</span> No contracts.
      </p>
      <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-16">
        <button onClick={onTryDemo} className="group px-8 py-3.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold text-sm flex items-center gap-2 hover:shadow-lg hover:shadow-teal-500/20 transition-all" data-testid="hero-cta">
          See It Live <Play className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
        </button>
        <button onClick={onGetStarted} className="px-8 py-3.5 rounded-full border border-teal-500/30 text-teal-400 font-medium text-sm hover:border-teal-400 hover:text-white transition-all" data-testid="hero-waitlist">
          Join the Waitlist <ArrowRight className="w-4 h-4 inline ml-1" />
        </button>
      </div>
      <div className="grid grid-cols-3 gap-6 max-w-md mx-auto">
        {[
          { val: '62%', label: 'Accuracy' },
          { val: '1.56', label: 'Sharpe Ratio' },
          { val: '276K', label: 'ML Samples' },
        ].map(s => (
          <div key={s.label} className="text-center">
            <div className="text-2xl sm:text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400">{s.val}</div>
            <div className="text-xs text-slate-400 mt-1">{s.label}</div>
          </div>
        ))}
      </div>
    </div>
  </section>
);

/* ─── Quant-Lite Callout ─── */
const QuantLiteCallout = () => (
  <section className="py-12 sm:py-16 border-t border-white/5" data-testid="landing-quant-callout">
    <div className="max-w-4xl mx-auto px-4 text-center">
      <p className="text-base sm:text-lg text-slate-300 leading-relaxed font-medium">
        With a <span className="text-white font-bold">GPT-5.2 powered post-mortem engine</span> and{' '}
        <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-cyan-400 font-bold">Adversarial AI</span>{' '}
        <span className="text-slate-400">(Strategist vs. Auditor)</span>, you aren&rsquo;t just a competitor&mdash;you are a{' '}
        <span className="text-white font-black italic">&ldquo;Quant-Lite&rdquo;</span>{' '}
        institutional stack for the price of a gym membership.
      </p>
    </div>
  </section>
);

/* ─── Commercial Video ─── */
const API_BASE = getApiBase();
const CommercialVideo = () => {
  const [video, setVideo] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/media/landing-video`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.has_video) setVideo(d); })
      .catch(() => {});
  }, []);

  if (!video) return null;

  return (
    <section className="py-16 sm:py-24 border-t border-white/5" data-testid="landing-commercial">
      <div className="max-w-4xl mx-auto px-4 text-center">
        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-teal-500/20 bg-teal-500/5 text-teal-400 text-xs font-medium mb-6">
          <Play className="w-3.5 h-3.5" /> See RISEDUAL AI in Action
        </div>
        <div className="rounded-2xl overflow-hidden border border-slate-700/50 bg-slate-900/60 shadow-2xl shadow-teal-900/10">
          <video
            src={`${API_BASE}/api/media/file/${video.file_id}`}
            controls
            autoPlay
            muted
            loop
            playsInline
            preload="auto"
            className="w-full aspect-video bg-black"
            data-testid="landing-video-player"
          />
        </div>
      </div>
    </section>
  );
};

/* ─── How It Works ─── */
const HowItWorks = () => (
  <section id="how-it-works" className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-how-it-works">
    <div className="max-w-6xl mx-auto px-4 sm:px-6">
      <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-4">The Adversarial Advantage</h2>
      <p className="text-sm text-slate-400 text-center max-w-xl mx-auto mb-16">
        Traditional AI fails because it learns from its own mistakes. RISEDUAL AI uses two competing models that challenge each other.
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
            icon: <Shield className="w-5 h-5 text-orange-400" />,
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
          <div key={card.title} className="p-6 rounded-xl border border-slate-800/60 bg-slate-800/50 hover:border-slate-400/30/60 transition-colors group">
            <div className="mb-4">{card.icon}</div>
            <h3 className="text-sm font-semibold text-white mb-2">{card.title}</h3>
            <p className="text-xs text-slate-400 mb-4 leading-relaxed">{card.desc}</p>
            <ul className="space-y-2">
              {card.bullets.map(b => (
                <li key={b} className="flex items-center gap-2 text-xs text-slate-400">
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
    { spec: 'Monthly Cost', us: '$55', them: '$99 - $416' },
    { spec: 'Contract Terms', us: 'No Contract', them: 'Annual Only' },
    { spec: 'AI Architecture', us: 'Adversarial (Strategist vs. Auditor)', them: 'Single-Model Black Box' },
    { spec: 'Self-Correction', us: 'Nightly Dual-Signal Retraining', them: 'Static Updates' },
    { spec: 'Post-Mortem Analysis', us: 'GPT-5.2 Classification', them: 'None / Generic' },
    { spec: 'Real-Time Data', us: 'Triple SSE + Whale Radar', them: 'Telegram / Delayed' },
  ];
  return (
    <section className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-comparison">
      <div className="max-w-4xl mx-auto px-4 sm:px-6">
        <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-3">
          RISEDUAL AI vs <span className="line-through decoration-red-500 decoration-2 text-slate-400">TradeAlgoGPT</span>
        </h2>
        <p className="text-sm text-slate-400 text-center mb-12">Stop paying institutional prices for retail-grade signals.</p>
        <div className="rounded-xl border border-slate-800/60 overflow-hidden overflow-x-auto">
          <table className="w-full text-xs min-w-[520px]">
            <thead>
              <tr className="border-b border-slate-600/30/60 bg-slate-800/50">
                <th className="text-left text-slate-400 font-medium px-4 py-3">Intelligence Specs</th>
                <th className="text-center text-teal-400 font-semibold px-4 py-3">RISEDUAL AI</th>
                <th className="text-center px-4 py-3"><span className="line-through decoration-red-500 decoration-2 text-slate-400">TradeAlgoGPT</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.spec} className={`border-b border-slate-600/30/30 ${i % 2 === 0 ? 'bg-slate-900/20' : ''}`}>
                  <td className="px-4 py-3 text-slate-400">{r.spec}</td>
                  <td className="px-4 py-3 text-center text-white font-medium">{r.us}</td>
                  <td className="px-4 py-3 text-center text-slate-400">{r.them}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid grid-cols-3 gap-4 mt-8">
          {[
            { icon: <Shield className="w-4 h-4 text-teal-400" />, title: 'The Auditor Veto', desc: 'Hunts for TECH_FAKEOUT and LIQUIDITY_GAP to kill bad trades.' },
            { icon: <Brain className="w-4 h-4 text-cyan-400" />, title: 'Pruned Memories', desc: 'Prunes toxic data nightly in ChromaDB for evolving accuracy.' },
            { icon: <Users className="w-4 h-4 text-teal-400" />, title: 'Zero Sales Calls', desc: 'Pay $55, get full RISEDUAL AI War Room access in under 60 seconds.' },
          ].map(c => (
            <div key={c.title} className="p-4 rounded-lg border border-slate-800/40 bg-slate-800/55">
              <div className="mb-2">{c.icon}</div>
              <h4 className="text-xs font-semibold text-white mb-1">{c.title}</h4>
              <p className="text-[10px] text-slate-400 leading-relaxed">{c.desc}</p>
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
    { icon: <Clock className="w-5 h-5" />, title: 'Nightly Dual-Signal Retraining', desc: 'Both Strategist and Auditor retrain every night on classified post-mortem data. Toxic patterns get pruned.' },
    { icon: <Radio className="w-5 h-5" />, title: 'Triple SSE Streams', desc: 'Real-time market data from three independent sources. No delays, no Telegram bots.' },
    { icon: <Zap className="w-5 h-5" />, title: 'Whale Radar', desc: 'Track large wallet movements and institutional accumulation before they hit mainstream news.' },
    { icon: <Brain className="w-5 h-5" />, title: 'GPT-5.2 Post-Mortem', desc: 'Every failed signal gets classified: TECH_FAKEOUT, NEWS_BOMB, LIQUIDITY_GAP. Labels feed back into training.' },
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
            <div key={f.title} className="p-6 rounded-xl border border-slate-800/50 bg-slate-800/55 hover:border-teal-500/20 transition-colors group">
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
    {/* max-w-6xl matches the Features / Comparison sections above and below
        so the pricing row doesn't visually "shrink" when scrolling past —
        the content column edges line up with siblings. */}
    <div className="max-w-6xl mx-auto px-4 sm:px-6">
      <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-2">Trade smarter with AI access built into every plan.</h2>
      <p className="text-xs text-slate-400 text-center mb-10 max-w-xl mx-auto">Every account includes AI access. Pro members get unlimited AI Chat and unlimited War Room, while advanced AI features use credits across all plans.</p>

      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-12">
        {/* Free */}
        <PlanCard
          name="Free" price="$0" period="/month" credits="50" creditsLabel="AI credits"
          desc="Great for exploring the platform and testing the AI workflow."
          features={['50 AI credits included', 'AI Chat (1 cr/msg)', 'Buy more credits anytime', 'Best for first-time traders']}
          cta="Start free" ctaStyle="border border-slate-600 text-slate-300 hover:bg-slate-700"
          onCta={onGetStarted} testId="pricing-free-cta"
        />
        {/* Starter */}
        <PlanCard
          name="Starter" price="$19" period="/month" credits="3,000" creditsLabel="AI credits"
          desc="Good for active users who want more AI access."
          features={['3,000 AI credits/month', 'Lower top-up rates ($12/1K)', 'AI Chat (1 cr/msg)', 'Best for regular research']}
          cta="Choose Starter" ctaStyle="border border-[#3DE8D9]/30 text-[#3DE8D9] hover:bg-[#3DE8D9]/10"
          onCta={onGetStarted} testId="pricing-starter-cta"
        />
        {/* Pro */}
        <PlanCard
          name="Pro" price="$55" period="/month" credits="15,000" creditsLabel="AI credits"
          desc="Best overall value for serious users."
          badge="Most Popular"
          features={['15,000 AI credits/month', 'Unlimited AI Chat', 'Unlimited War Room', 'Better top-up rates ($8/1K)', 'Full adversarial AI engine']}
          cta="Go Pro" ctaStyle="bg-gradient-to-r from-teal-500 to-cyan-500 text-white hover:opacity-90"
          highlight onCta={onGetStarted} testId="pricing-cta"
        />
        {/* Pro Max */}
        <PlanCard
          name="Pro Max" price="$99" period="/month" credits="50,000" creditsLabel="AI credits"
          desc="Built for high-volume AI and API usage."
          features={['50,000 AI credits/month', 'Unlimited AI Chat', 'Unlimited War Room', 'Lowest top-up rates ($5/1K)', 'Best for power traders']}
          cta="Get Pro Max" ctaStyle="border border-violet-500/30 text-violet-400 hover:bg-violet-500/10"
          onCta={onGetStarted} testId="pricing-promax-cta"
        />
      </div>

      {/* Action Pricing Table */}
      <div className="mt-10 mb-8">
        <h3 className="text-white text-xs font-bold mb-3 text-center">Action pricing</h3>
        <p className="text-slate-400 text-[10px] text-center mb-4 max-w-xl mx-auto">Chat and War Room are bundled into Pro and Pro Max because they are your most frequent workflows. Advanced AI actions stay credit-based on every plan.</p>
        <div className="overflow-x-auto">
          <table className="w-full text-[10px] border-collapse table-fixed" data-testid="action-pricing-table">
            <colgroup>
              <col style={{ width: '24%' }} />
              <col style={{ width: '19%' }} />
              <col style={{ width: '19%' }} />
              <col style={{ width: '19%' }} />
              <col style={{ width: '19%' }} />
            </colgroup>
            <thead>
              <tr className="text-slate-400 border-b border-slate-700/40">
                <th className="text-left py-2 px-3">Action</th>
                <th className="text-center py-2 px-3">Free</th>
                <th className="text-center py-2 px-3">Starter</th>
                <th className="text-center py-2 px-3">Pro</th>
                <th className="text-center py-2 px-3">Pro Max</th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              {[
                ['AI Chat', '1 credit', '1 credit', true, true],
                ['War Room', '5 credits', '5 credits', true, true],
                ['AI Hypothesis', '3 credits', '3 credits', '3 credits', '3 credits'],
                ['Market Prediction', '3 credits', '3 credits', '3 credits', '3 credits'],
                ['AI Intelligence', '2 credits', '2 credits', '2 credits', '2 credits'],
                ['Scanner + Validation', '2 credits', '2 credits', '2 credits', '2 credits'],
                ['API call', '1 credit', '1 credit', '1 credit', '1 credit'],
              ].map(([action, ...vals]) => (
                <tr key={action} className="border-b border-slate-800/40">
                  <td className="py-2 px-3 text-white font-medium break-words">{action}</td>
                  {vals.map((v, i) => (
                    <td key={`${action}-${i}`} className={`text-center py-2 px-3 ${v === true ? 'text-lime-400 font-bold' : ''}`}>
                      {v === true ? 'Unlimited' : v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Top-up Pricing Table */}
      <div className="mb-10 overflow-x-auto">
        <h3 className="text-white text-xs font-bold mb-3 text-center">Top-up pricing</h3>
        <table className="w-full max-w-md mx-auto text-[10px] border-collapse" data-testid="topup-pricing-table">
          <thead>
            <tr className="text-slate-400 border-b border-slate-700/40">
              <th className="text-left py-2 px-3">Plan</th>
              <th className="text-center py-2 px-3">Included credits</th>
              <th className="text-center py-2 px-3">Top-up per 1,000</th>
            </tr>
          </thead>
          <tbody className="text-slate-300">
            {[
              ['Free', '50', '$15'],
              ['Starter', '3,000', '$12'],
              ['Pro', '15,000', '$8'],
              ['Pro Max', '50,000', '$5'],
            ].map(([plan, credits, rate]) => (
              <tr key={plan} className="border-b border-slate-800/40">
                <td className="py-2 px-3 text-white font-medium">{plan}</td>
                <td className="text-center py-2 px-3">{credits}</td>
                <td className="text-center py-2 px-3 text-amber-400 font-semibold">{rate}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Why Pro */}
      <div className="bg-slate-800/40 border border-slate-700/40 rounded-xl p-5 mb-8 max-w-2xl mx-auto">
        <h3 className="text-white text-xs font-bold mb-2">Why Pro stands out</h3>
        <p className="text-slate-400 text-[10px] leading-relaxed">If you use AI Chat and War Room often, Pro quickly becomes the best value because your most frequent workflows no longer consume credits. Pro includes unlimited Chat and War Room. Advanced AI actions use credits on every plan.</p>
      </div>

      {/* How pricing works */}
      <div className="max-w-2xl mx-auto">
        <h3 className="text-white text-xs font-bold mb-3 text-center">How pricing works</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-[10px]">
          {[
            'AI access is available on every plan',
            'Free, Starter, Pro, and Pro Max all include credits',
            'Pro and Pro Max include unlimited AI Chat and War Room',
            'Advanced AI actions still use credits on every plan',
            'Buy extra credits anytime — higher plans get better rates',
            'Founding 50 members get Pro pricing for life',
          ].map(t => (
            <div key={t} className="flex items-start gap-1.5 bg-slate-800/30 rounded-lg p-2">
              <Check className="w-3 h-3 text-[#3DE8D9] shrink-0 mt-0.5" />
              <span className="text-slate-400">{t}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  </section>
);

const PlanCard = ({ name, price, period, credits, creditsLabel, desc, badge, features, cta, ctaStyle, highlight, onCta, testId }) => (
  // Equal border width on all four tiers (2px on everyone — transparent for
  // non-highlighted) prevents the 1px-vs-2px pixel shift that caused Pro to
  // sit lower than its neighbors in a 4-col row. `flex flex-col` + `h-full`
  // makes every card stretch to the tallest sibling so the CTA buttons line
  // up at the bottom regardless of how many feature bullets each plan has.
  <div className={`relative flex flex-col h-full p-5 rounded-xl border-2 ${highlight ? 'border-teal-500/40 bg-slate-800/60' : 'border-transparent bg-slate-800/30 ring-1 ring-slate-700/40'}`}>
    {badge && (
      <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-0.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-[9px] text-white font-bold uppercase tracking-wider whitespace-nowrap">
        {badge}
      </div>
    )}
    <h3 className="text-sm font-bold text-white mb-1">{name}</h3>
    <div className="flex items-baseline gap-1 mb-1">
      <span className="text-2xl font-bold text-white">{price}</span>
      <span className="text-[10px] text-slate-400">{period}</span>
    </div>
    <p className="text-amber-400 text-[10px] font-semibold mb-2">{credits} {creditsLabel}</p>
    <p className="text-slate-400 text-[10px] mb-4 leading-relaxed">{desc}</p>
    <ul className="space-y-1.5 mb-4 flex-grow">
      {features.map(f => (
        <li key={f} className="flex items-start gap-1.5 text-[10px] text-slate-300">
          <Check className="w-3 h-3 text-teal-400 shrink-0 mt-0.5" />
          {f}
        </li>
      ))}
    </ul>
    <button onClick={onCta} className={`w-full py-2 rounded-lg text-xs font-semibold transition-all ${ctaStyle}`} data-testid={testId}>
      {cta}
    </button>
  </div>
);

/* ─── FAQ ─── */
const FAQ = () => {
  const [open, setOpen] = useState(null);
  const items = [
    { q: 'What makes RISEDUAL AI different from other trading signals?', a: 'RISEDUAL AI uses an adversarial AI architecture with two competing models. The Strategist generates trade signals, and the Auditor actively tries to disprove them. This dual-signal approach catches false breakouts, liquidity traps, and regime shifts that single-model systems miss.' },
    { q: 'How does the nightly retraining work?', a: 'Every night, GPT-5.2 analyzes all failed signals and classifies them (TECH_FAKEOUT, NEWS_BOMB, LIQUIDITY_GAP, etc.). These toxic patterns are pruned from ChromaDB, and winning patterns are reinforced. Both the Strategist and Auditor retrain on this refined dataset.' },
    { q: 'Do I need to sign an annual contract?', a: 'No. Plans start at $0 (Free), $19 (Starter), $55 (Pro), or $99 (Pro Max) per month. No contracts. Cancel anytime from your dashboard. Founding 50 members are locked in at Pro pricing for life.' },
    { q: 'What happens when I run out of credits?', a: 'You can buy more credits anytime. Unlimited AI Chat and War Room remain available for Pro and Pro Max members even at zero credits. Higher plans get better top-up rates.' },
    { q: 'Do credits expire?', a: 'Credits reset monthly with your plan renewal. Unused credits do not roll over. You can always buy top-ups if you need more mid-cycle.' },
    { q: 'Why are some features unlimited and others credit-based?', a: 'Chat and War Room are core daily workflows, so they are bundled into Pro and Pro Max as unlimited. Heavier AI actions like Hypothesis and Predictions still use credits so pricing stays sustainable and flexible.' },
    { q: 'What markets and assets do you cover?', a: 'US stocks (S&P 500, NASDAQ), major cryptocurrencies (BTC, ETH, SOL, etc.), options flow, dark pool data, and macro indicators including sector heatmaps and congressional trading activity.' },
    { q: 'Is there a free trial?', a: 'Yes. You can start with a 7-day free trial that gives you full access to the War Room, AI agents, Whale Radar, and all real-time data streams.' },
    { q: 'How accurate are the signals?', a: 'Our adversarial system has achieved a verified 68-73% win rate across backtested periods. The Auditor\'s veto mechanism kills approximately 40% of signals before they reach you, significantly reducing false positives.' },
  ];
  return (
    <section id="faq" className="py-20 sm:py-28 border-t border-white/5" data-testid="landing-faq">
      <div className="max-w-3xl mx-auto px-4 sm:px-6">
        <h2 className="text-base sm:text-lg font-semibold text-white text-center mb-3">Frequently Asked Questions</h2>
        <p className="text-sm text-slate-400 text-center mb-14">Everything you need to know about RISEDUAL AI.</p>
        <div className="space-y-3">
          {items.map((item, i) => (
            <div key={item.q} className="rounded-xl border border-slate-800/50 bg-slate-800/55 overflow-hidden">
              <button
                onClick={() => setOpen(open === i ? null : i)}
                className="w-full flex items-center justify-between px-5 py-4 text-left"
                data-testid={`faq-${i}`}
              >
                <span className="text-xs font-medium text-white pr-4">{item.q}</span>
                <ChevronDown className={`w-4 h-4 text-slate-400 shrink-0 transition-transform ${open === i ? 'rotate-180' : ''}`} />
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
        Join hundreds of traders who stopped paying $99-$416/month for single-model black boxes. Get the full War Room for $55.
      </p>
      <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-8">
        <button onClick={onGetStarted} className="group px-8 py-3.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold text-sm flex items-center gap-2 hover:shadow-lg hover:shadow-teal-500/20 transition-all" data-testid="cta-final">
          Join the Waitlist <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
        </button>
      </div>
      <div className="flex items-center justify-center gap-6 text-[10px] text-slate-400">
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
const LandingFooter = ({ onOpenLegal }) => (
  <footer className="border-t border-white/5 py-8">
    <div className="max-w-6xl mx-auto px-4">
      {/* Investment Risk Disclosure */}
      <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 mb-6" data-testid="footer-risk-disclosure">
        <p className="text-amber-400 text-[10px] font-bold uppercase tracking-wider mb-2">Investment Risk Disclosure</p>
        <ul className="space-y-1.5 text-slate-400 text-[10px] leading-relaxed">
          <li><strong className="text-slate-300">High Risk Warning:</strong> Trading stocks, options, and digital assets involves significant risk of loss.</li>
          <li><strong className="text-slate-300">No Financial Advice:</strong> RISEDUAL AI is a <strong className="text-slate-300">financial research publishing platform</strong>. All content, including AI-generated signals and "4-Mind" insights, is for informational and educational purposes only.</li>
          <li><strong className="text-slate-300">Not a Broker/Adviser:</strong> RISEDUAL AI and RISEDUAL INC. are not registered investment advisers (RIAs) or broker-dealers. We do not provide personalized investment recommendations.</li>
          <li><strong className="text-slate-300">AI Limitations:</strong> Content is generated with assistance from AI models (GPT-5.2). AI can "hallucinate" or provide inaccurate data. Users must perform their own due diligence before executing any trade.</li>
          <li><strong className="text-slate-300">Past Performance:</strong> Any displayed backtests or historical results are not indicative of future performance.</li>
        </ul>
      </div>

      <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
        <span className="text-xs text-slate-400">&copy; {new Date().getFullYear()} RISEDUAL INC. All rights reserved.</span>
        <div className="flex items-center gap-6 text-xs text-slate-400">
          <button onClick={() => onOpenLegal?.('privacy')} className="hover:text-slate-300 transition-colors" data-testid="landing-privacy-link">Privacy</button>
          <button onClick={() => onOpenLegal?.('terms')} className="hover:text-slate-300 transition-colors" data-testid="landing-terms-link">Terms</button>
          <button onClick={() => onOpenLegal?.('risk')} className="hover:text-slate-300 transition-colors" data-testid="landing-risk-link">Risk Disclosure</button>
          <button onClick={() => onOpenLegal?.('disclaimer')} className="hover:text-slate-300 transition-colors" data-testid="landing-disclaimer-link">Disclaimer</button>
          <button onClick={() => onOpenLegal?.('security')} className="hover:text-slate-300 transition-colors" data-testid="landing-security-link">Security</button>
          <a href="/compliance/oauth" className="hover:text-slate-300 transition-colors" data-testid="landing-compliance-link">Compliance</a>
        </div>
      </div>
    </div>
  </footer>
);

import AboutUs from './AboutUs';

/* ─── Main Landing Page ─── */
const LandingPage = ({ onGetStarted, onLogin, onOpenLegal, onTryDemo }) => {
  const scrollToHow = () => {
    document.getElementById('how-it-works')?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="min-h-screen bg-slate-950 text-white overflow-x-hidden" data-testid="landing-page">
      <Header onGetStarted={onGetStarted} onLogin={onLogin} onTryDemo={onTryDemo} />
      <Hero onGetStarted={onGetStarted} onScroll={scrollToHow} onTryDemo={onTryDemo} />
      <QuantLiteCallout />
      <CommercialVideo />
      <HowItWorks />
      <Comparison />
      <Features />
      <AboutUs embedded />
      <Pricing onGetStarted={onGetStarted} />
      <FAQ />
      <CTA onGetStarted={onGetStarted} />
      <LandingFooter onOpenLegal={onOpenLegal} />
    </div>
  );
};

export default LandingPage;
