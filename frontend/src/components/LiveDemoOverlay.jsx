import React, { useState, useEffect, useRef } from 'react';
import { TrendingUp, TrendingDown, Brain, BarChart3, Activity, Database, Zap, ArrowRight, X, Shield, Lock, MessageSquare, Send, Loader2 } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const API = getApiBase();

const Sparkline = ({ up }) => {
  const d = up
    ? 'M0,20 L8,18 L16,14 L24,16 L32,10 L40,12 L48,6 L56,8 L64,2'
    : 'M0,4 L8,6 L16,10 L24,8 L32,14 L40,12 L48,18 L56,16 L64,20';
  return (
    <svg width="64" height="22" className="shrink-0">
      <path d={d} fill="none" stroke={up ? '#84cc16' : '#f97316'} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
};

const DemoChat = () => {
  const [messages, setMessages] = useState([
    { role: 'assistant', content: "Welcome to RISEDUAL AI! I'm your trading assistant. Ask me about markets, our ML signals (62% accuracy, Sharpe 1.56), or how the platform works." }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [remaining, setRemaining] = useState(20);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput('');
    const userMsg = { role: 'user', content: text };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/demo/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: newMessages.filter(m => m.role !== 'assistant' || newMessages.indexOf(m) > 0).slice(-6) }),
      });
      const data = await res.json();
      if (data.response) {
        setMessages(prev => [...prev, { role: 'assistant', content: data.response }]);
        if (data.remaining != null) setRemaining(data.remaining);
      } else if (data.limit) {
        setMessages(prev => [...prev, { role: 'assistant', content: "You've reached the demo limit! Join the waitlist for unlimited AI access with GPT-5.2." }]);
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: data.error || "Something went wrong. Try again!" }]);
      }
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: "Connection error. Please try again." }]);
    }
    setLoading(false);
  };

  const suggestions = ['What is a Sharpe ratio?', 'How does RISEDUAL detect signals?', 'Explain the FRED data', 'What markets do you cover?'];

  return (
    <div className="bg-slate-800/70 border border-slate-700/40 rounded-xl overflow-hidden" data-testid="demo-chat">
      <div ref={scrollRef} className="h-64 overflow-y-auto p-4 space-y-3">
        {messages.map((m, i) => (
          <div key={`msg-${m.role}-${i}-${m.content.slice(0,12)}`} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-xl px-3.5 py-2.5 text-sm ${
              m.role === 'user'
                ? 'bg-[#3DE8D9]/20 text-[#3DE8D9]'
                : 'bg-slate-700/60 text-slate-200'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-slate-700/60 rounded-xl px-3.5 py-2.5">
              <Loader2 className="w-4 h-4 text-[#3DE8D9] animate-spin" />
            </div>
          </div>
        )}
      </div>
      {/* Suggestions */}
      {messages.length <= 2 && (
        <div className="px-4 pb-2 flex flex-wrap gap-1.5">
          {suggestions.map(s => (
            <button key={s} onClick={() => { setInput(s); }}
              className="text-[10px] px-2.5 py-1 rounded-full bg-slate-700/50 text-slate-400 hover:text-white hover:bg-slate-600/50 transition-colors">
              {s}
            </button>
          ))}
        </div>
      )}
      {/* Input */}
      <div className="border-t border-slate-700/40 p-3 flex gap-2">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send()}
          placeholder="Ask about markets, signals, or the platform..."
          className="flex-1 bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-[#3DE8D9]/50"
          data-testid="demo-chat-input"
        />
        <button onClick={send} disabled={loading || !input.trim()}
          className="bg-[#3DE8D9] hover:bg-[#2fd4c6] text-slate-900 rounded-lg px-3 py-2 disabled:opacity-40 transition-colors"
          data-testid="demo-chat-send">
          <Send className="w-4 h-4" />
        </button>
      </div>
      <div className="px-4 pb-2 flex items-center justify-between">
        <span className="text-slate-600 text-[9px]">NVIDIA Nemotron Nano 9B · {remaining} messages remaining</span>
        <span className="text-slate-600 text-[9px]">Not financial advice</span>
      </div>
    </div>
  );
};

export default function LiveDemoOverlay({ onClose, onJoinWaitlist }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API}/api/demo/dashboard`);
        if (res.ok) setData(await res.json());
      } catch (e) { /* ignore */ }
      setLoading(false);
    })();
  }, []);

  const ml = data?.ml || {};
  const predictions = data?.predictions || [];
  const fred = data?.fred || [];
  const paper = data?.paper_stats || {};
  const platform = data?.platform || {};

  return (
    <div className="fixed inset-0 z-[60] bg-[#060E1F] overflow-y-auto" data-testid="live-demo-overlay">
      {/* Top bar */}
      <div className="sticky top-0 z-10 bg-slate-900/95 backdrop-blur-sm border-b border-slate-700/50 px-4 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-[#3DE8D9]/20 flex items-center justify-center">
            <span className="text-[#3DE8D9] font-bold text-xs">R</span>
          </div>
          <span className="text-white font-bold text-sm">RISEDUAL AI</span>
          <span className="bg-amber-500/15 text-amber-400 text-[10px] px-2 py-0.5 rounded-full font-medium">LIVE DEMO</span>
        </div>
        <div className="flex items-center gap-3">
          <button onClick={onJoinWaitlist}
            className="text-sm px-5 py-2 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold hover:opacity-90 transition-opacity"
            data-testid="demo-join-waitlist">
            Join Waitlist <ArrowRight className="w-3.5 h-3.5 inline ml-1" />
          </button>
          <button onClick={onClose} className="text-slate-400 hover:text-white p-1" data-testid="demo-close">
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-[60vh]">
          <div className="text-center">
            <Activity className="w-8 h-8 text-[#3DE8D9] animate-pulse mx-auto mb-3" />
            <p className="text-slate-400 text-sm">Loading live platform data...</p>
          </div>
        </div>
      ) : (
        <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">

          {/* ML Model Stats */}
          <div>
            <h2 className="text-white font-bold text-lg mb-3 flex items-center gap-2">
              <Brain className="w-5 h-5 text-[#3DE8D9]" /> ML Signal Engine
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
              {[
                { label: 'Model', value: `XGBoost ${ml.model_version}`, color: '#3DE8D9' },
                { label: 'Accuracy', value: `${ml.accuracy}%`, color: '#84cc16' },
                { label: 'Sharpe Ratio', value: ml.sharpe?.toFixed(2), color: '#3DE8D9' },
                { label: 'Max Drawdown', value: `${ml.max_drawdown}%`, color: '#f97316' },
                { label: 'Features', value: ml.features, color: '#8B5CF6' },
                { label: 'Status', value: 'Tier 2 Active', color: '#84cc16' },
              ].map(s => (
                <div key={s.label} className="bg-slate-800/70 border border-slate-700/40 rounded-xl p-3.5">
                  <p className="text-slate-400 text-[10px] uppercase tracking-wider">{s.label}</p>
                  <p className="font-bold text-lg mt-1" style={{ color: s.color }}>{s.value}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Live AI Predictions */}
          <div>
            <h2 className="text-white font-bold text-lg mb-3 flex items-center gap-2">
              <Zap className="w-5 h-5 text-amber-400" /> Live AI Predictions
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {predictions.map((p, i) => {
                const up = p.direction === 'BULL' || p.direction === 'bullish' || p.direction === 'up';
                return (
                  <div key={p.symbol || i} className="bg-slate-800/70 border border-slate-700/40 rounded-xl p-3.5 flex items-center justify-between">
                    <div>
                      <p className="text-white font-bold text-sm">{p.symbol}</p>
                      <div className="flex items-center gap-1.5 mt-1">
                        {up ? <TrendingUp className="w-3.5 h-3.5 text-lime-400" /> : <TrendingDown className="w-3.5 h-3.5 text-orange-400" />}
                        <span className={`text-xs font-semibold ${up ? 'text-lime-400' : 'text-orange-400'}`}>
                          {(p.confidence * 100).toFixed(0)}% {up ? 'Bull' : 'Bear'}
                        </span>
                      </div>
                    </div>
                    <Sparkline up={up} />
                  </div>
                );
              })}
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* FRED Macro Indicators */}
            <div>
              <h2 className="text-white font-bold text-lg mb-3 flex items-center gap-2">
                <Activity className="w-5 h-5 text-blue-400" /> FRED Economic Data
              </h2>
              <div className="bg-slate-800/70 border border-slate-700/40 rounded-xl divide-y divide-slate-700/30">
                {fred.map(f => (
                  <div key={f.id} className="flex items-center justify-between px-4 py-3">
                    <div>
                      <p className="text-white text-sm font-medium">{f.name?.replace(/\(.*\)/, '').trim().slice(0, 30)}</p>
                      <p className="text-slate-500 text-[10px]">{f.category} · {f.id}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-white font-bold text-sm">{f.value}{f.unit && f.unit !== '$T' && f.unit !== '$B' ? f.unit : ''}{f.unit === '$T' ? 'T' : f.unit === '$B' ? 'B' : ''}</p>
                      {f.change_pct != null && (
                        <p className={`text-[10px] ${f.change_pct >= 0 ? 'text-lime-400' : 'text-orange-400'}`}>
                          {f.change_pct >= 0 ? '+' : ''}{f.change_pct}%
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Paper Trading Performance + Platform Stats */}
            <div className="space-y-5">
              <div>
                <h2 className="text-white font-bold text-lg mb-3 flex items-center gap-2">
                  <BarChart3 className="w-5 h-5 text-emerald-400" /> Paper Trading Performance
                </h2>
                <div className="grid grid-cols-3 gap-3">
                  <div className="bg-slate-800/70 border border-slate-700/40 rounded-xl p-3.5">
                    <p className="text-slate-400 text-[10px] uppercase">Total Trades</p>
                    <p className="text-white font-bold text-xl mt-1">{paper.total_trades?.toLocaleString()}</p>
                  </div>
                  <div className="bg-slate-800/70 border border-slate-700/40 rounded-xl p-3.5">
                    <p className="text-slate-400 text-[10px] uppercase">Win Rate</p>
                    <p className="text-lime-400 font-bold text-xl mt-1">{paper.win_rate}%</p>
                  </div>
                  <div className="bg-slate-800/70 border border-slate-700/40 rounded-xl p-3.5">
                    <p className="text-slate-400 text-[10px] uppercase">Total P&L</p>
                    <p className={`font-bold text-xl mt-1 ${paper.total_pnl >= 0 ? 'text-lime-400' : 'text-orange-400'}`}>
                      ${paper.total_pnl?.toLocaleString()}
                    </p>
                  </div>
                </div>
              </div>

              <div>
                <h2 className="text-white font-bold text-lg mb-3 flex items-center gap-2">
                  <Database className="w-5 h-5 text-violet-400" /> Data Pipeline
                </h2>
                <div className="grid grid-cols-3 gap-3">
                  <div className="bg-slate-800/70 border border-slate-700/40 rounded-xl p-3.5">
                    <p className="text-slate-400 text-[10px] uppercase">ML Samples</p>
                    <p className="text-white font-bold text-lg mt-1">{(platform.training_samples / 1000).toFixed(0)}K</p>
                  </div>
                  <div className="bg-slate-800/70 border border-slate-700/40 rounded-xl p-3.5">
                    <p className="text-slate-400 text-[10px] uppercase">Tickers</p>
                    <p className="text-white font-bold text-lg mt-1">{platform.tickers_covered}</p>
                  </div>
                  <div className="bg-slate-800/70 border border-slate-700/40 rounded-xl p-3.5">
                    <p className="text-slate-400 text-[10px] uppercase">History</p>
                    <p className="text-white font-bold text-lg mt-1">{platform.data_years}yr</p>
                  </div>
                </div>
              </div>

              {/* Security badges */}
              <div className="bg-slate-800/40 border border-slate-700/30 rounded-xl p-4 space-y-2">
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <Shield className="w-3.5 h-3.5 text-[#3DE8D9]" /> OAuth2 brokerage connections — we never store passwords
                </div>
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <Lock className="w-3.5 h-3.5 text-[#3DE8D9]" /> AES-256 encrypted KeyVault for all credentials
                </div>
              </div>
            </div>
          </div>

          {/* AI Demo Chat */}
          <div>
            <h2 className="text-white font-bold text-lg mb-3 flex items-center gap-2">
              <MessageSquare className="w-5 h-5 text-[#3DE8D9]" /> Ask the AI
              <span className="text-slate-500 text-xs font-normal ml-1">Powered by NVIDIA Nemotron</span>
            </h2>
            <DemoChat />
          </div>

          {/* CTA Banner */}
          <div className="bg-gradient-to-r from-teal-500/10 to-cyan-500/10 border border-teal-500/20 rounded-2xl p-6 sm:p-8 text-center">
            <h3 className="text-white text-xl sm:text-2xl font-bold mb-2">Ready to trade with AI?</h3>
            <p className="text-slate-400 text-sm mb-5 max-w-lg mx-auto">
              Join the waitlist for early access to RISEDUAL AI — ML signals, autonomous trading, FRED macro intelligence, and more. $55/month, no contracts.
            </p>
            <button onClick={onJoinWaitlist}
              className="px-8 py-3.5 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold text-sm hover:shadow-lg hover:shadow-teal-500/20 transition-all"
              data-testid="demo-cta-waitlist">
              Join the Waitlist <ArrowRight className="w-4 h-4 inline ml-1" />
            </button>
          </div>

          <p className="text-slate-600 text-[10px] text-center pb-4">
            All data shown is live from the RISEDUAL platform. Not financial advice. Past performance does not guarantee future results.
          </p>
        </div>
      )}
    </div>
  );
}
