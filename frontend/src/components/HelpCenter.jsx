import React, { useState, useEffect, useMemo, useRef } from 'react';
import {
  X, HelpCircle, Sparkles, Swords, Search as SearchIcon, Briefcase, Keyboard,
  BarChart3, Zap, Info, BookOpen, ArrowRight,
} from 'lucide-react';
import IconTabBar from './hubs/IconTabBar';
import useV2Nav from '../hooks/useV2Nav';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

/* ───────────────────────────  Content library  ──────────────────────────── */
/* Every tip can optionally deep-link via { goto: ['view', 'subTab'] }        */

const SECTIONS = [
  {
    key: 'start', label: 'Getting Started', icon: BookOpen, accent: '#3DE8D9',
    desc: 'Your first five minutes on RISEDUAL AI',
    items: [
      { title: 'The 5 top-level hubs',
        content: 'Dashboard · War Room · Research · Options · Workspace. Each hub has its own icon tab strip. Click the ⓘ icon inside any hub to see what every icon does — no memorising required.',
        goto: ['dashboard'] },
      { title: 'v2 UI vs Classic UI',
        content: 'v2 is the current default. A "Classic UI" pill in the top-right flips you to the historical design if you prefer it. Everything is preserved — nothing deleted.' },
      { title: 'Paper vs Live trading',
        content: 'Every new account starts with $100K paper capital. Live (real-money) execution is gated behind 30 days of paper trading + Sharpe 1.2 + the ML Tier 3 calibration gate.',
        goto: ['workspace', 'paper'] },
      { title: 'Subscription tiers',
        content: 'Free: watchlist + basic data. Pro ($45/mo): AI predictions, dark pool, smart orders, bots, scanner. Pro Max: everything + 50K monthly AI credits + unlimited War Room & chat.' },
      { title: 'AI credits',
        content: 'Each AI task (prediction, chat, hypothesis, scan validate) costs credits. Pro Max plan refills 50K monthly. Check your balance in Workspace → Credits.',
        goto: ['workspace', 'credits'] },
    ],
  },
  {
    key: 'warroom', label: 'War Room', icon: Swords, accent: '#fdba74',
    desc: 'The adversarial AI command center',
    items: [
      { title: 'Adversarial AI loop',
        content: 'Strategist proposes trades → Auditor stress-tests them → only signals that survive both pass through. This is the core differentiator — dual models critiquing each other.',
        goto: ['warroom', 'adversarial'] },
      { title: 'Predictions sub-tab',
        content: 'Raw AI verdicts on individual tickers or the broader market. Confidence scores, accuracy tracking, and full reasoning chains.',
        goto: ['warroom', 'prediction'] },
      { title: 'Hypothesis generator',
        content: 'Enter a ticker. Get a bull + bear thesis before the prediction is made. Consensus mode runs 3 models and shows agreement/disagreement.',
        goto: ['warroom', 'hypothesis'] },
      { title: 'Signals (technical + flow)',
        content: 'Live technical signals (RSI/MACD/BB) merged with flow signals (unusual options, dark-pool prints). Filter by ticker or watchlist.',
        goto: ['warroom', 'signals'] },
      { title: 'Intelligence panel',
        content: 'Multi-model consensus engine with episodic memory recall. Shows what the AI learned from past predictions and how it has updated its priors.',
        goto: ['warroom', 'intelligence'] },
    ],
  },
  {
    key: 'research', label: 'Research', icon: SearchIcon, accent: '#3DE8D9',
    desc: 'Deep-dive on tickers, macro, and strategies',
    items: [
      { title: 'Stock Detail (one search, three views)',
        content: 'Enter a ticker once → Overview (AI company research), Fundamentals (SEC EDGAR income/balance/F-Score/Z-Score), and 13F Holders (institutional positions + quarterly changes) all populate together.',
        goto: ['research', 'stock'] },
      { title: 'Macro dashboard',
        content: 'Global rates, FX, commodities, yield curves, VIX. Click any chart to deep-dive.',
        goto: ['research', 'macro'] },
      { title: 'Strategy Builder',
        content: 'Compose custom entry/exit rules with 23+ indicators. Backtest against historical data. Save & deploy as a bot.',
        goto: ['research', 'strategy'] },
      { title: 'Strategy Marketplace',
        content: 'Browse community-shared strategies. See win rate, Sharpe, max drawdown, number of trades before copying.',
        goto: ['research', 'marketplace'] },
      { title: 'AI Memory dashboard',
        content: 'Inspect what the AI has stored in its vector memory — episodic predictions, lessons learned, negative examples it avoids repeating.',
        goto: ['research', 'memory'] },
    ],
  },
  {
    key: 'options', label: 'Flow', icon: BarChart3, accent: '#c4b5fd',
    desc: 'Options, dark pool, and institutional flow',
    items: [
      { title: 'Options Radar',
        content: 'Unusual options activity with AI scoring. Filter by ticker, volume/OI ratio, and power score. Click Buy/Sell to route through Smart Orders.',
        goto: ['options', 'radar'] },
      { title: 'Options Flow Screener',
        content: 'Real-time block trades, sweeps, and premium flow. Surface unusual bullish/bearish positioning by strike and expiry.',
        goto: ['options', 'flow'] },
      { title: 'Dark Pool',
        content: 'Off-exchange institutional prints via Polygon.io. See where large volume is accumulating that the tape doesn\'t show.',
        goto: ['options', 'darkpool'] },
    ],
  },
  {
    key: 'workspace', label: 'Workspace', icon: Briefcase, accent: '#fbbf24',
    desc: 'Your personal trading cockpit (13 tools)',
    items: [
      { title: 'Icon legend (ⓘ button)',
        content: 'Workspace has 13 tools — Watchlist, Portfolio, Journal, P&L, Paper, Bots, ML Controls, Smart Orders, Risk Calc, Scanner, Credits, Referrals, Loops. Hover an icon for its name. Click the ⓘ at the right of the tab row for the full legend.' },
      { title: 'Paper trading ($100K virtual)',
        content: 'Practise with real-time prices and simulated fills. P&L tracked separately from live. 30 days of paper is one of the gates to live execution.',
        goto: ['workspace', 'paper'] },
      { title: 'Smart Orders',
        content: 'Ladder entries, trailing SL/TP, break-even protection, up to 5 TP levels each selling a % of the position. Paper or (gated) live.',
        goto: ['workspace', 'orders'] },
      { title: 'Risk Calculator',
        content: 'Enter entry/SL/TP → get exact position size by %-risk, fixed-dollar, or Kelly Criterion. "Apply to Smart Order" transfers values straight in.',
        goto: ['workspace', 'risk'] },
      { title: 'Market Scanner + AI Validate',
        content: '10 preset scans (RSI, MACD cross, BB squeeze, golden cross, volume spike, 52W high/low…) or build custom rules with AND/OR logic. Click AI Validate to stress-test any match.',
        goto: ['workspace', 'scanner'] },
      { title: 'Trading Bots (start OFF)',
        content: 'Every new bot is OFF by default — flip the green switch to activate. Grid bot, Signal bot, TradingView webhook bot. Paper mode until you explicitly allow live.',
        goto: ['workspace', 'bots'] },
      { title: 'ML Controls',
        content: 'View and adjust the ML pipeline tiers, training schedules, and calibration gates. Advanced/admin.',
        goto: ['workspace', 'ml'] },
      { title: 'Trading Journal',
        content: 'Log every trade (entry, exit, thesis, lessons). Tag by strategy. Searchable. Export CSV.',
        goto: ['workspace', 'journal'] },
      { title: 'Failure Loops',
        content: 'Nightly job flags repeated high-confidence misses and re-tags them as "negative lessons" in AI memory so the models stop making the same mistake.',
        goto: ['workspace', 'failureloop'] },
    ],
  },
  {
    key: 'data', label: 'Data & Signals', icon: BarChart3, accent: '#3DE8D9',
    desc: 'What\'s on the dashboard and how to read it',
    items: [
      { title: 'Watchlist + Smart Money scores',
        content: 'Each watchlist row shows an "SM" badge (0–100) from institutional 13F aggregation. Green ≥70 bullish, red ≤30 bearish. 30-day sparkline shows the trend.' },
      { title: 'Share My Smart Money Board',
        content: 'One-click PNG export of your board (html2canvas) with a trackable referral QR code. Hit 5 scans in a month → free Pro trial. Monthly #1 → Pro Max.' },
      { title: 'Sector Heatmap',
        content: 'S&P 500 sector ETF performance heatmap. 1D/1W/1M/3M/YTD timeframes. Click any tile to drill into its constituents.' },
      { title: 'Fear & Greed Gauge',
        content: '0–100 composite sentiment indicator. Useful contrarian signal at extremes (under 20 or over 80).' },
      { title: 'Whale Radar + Order Flow',
        content: 'Live whale transaction monitor for crypto, plus bid/ask order-flow heatmap for equities. Both on the Dashboard.' },
      { title: 'Alerts (bell icon)',
        content: 'Regime-shift alerts fire when a ticker\'s Smart Money score moves more than 10 points in a day. VAPID push supported.' },
    ],
  },
  {
    key: 'shortcuts', label: 'Shortcuts', icon: Keyboard, accent: '#3DE8D9',
    desc: 'Keyboard and URL shortcuts',
    items: [
      { title: '⌘K / Ctrl+K — Global search',
        content: 'Focuses the symbol search from anywhere. Type a ticker, hit Enter, jumps to Stock Detail.' },
      { title: '?v1=1 — Classic UI',
        content: 'Append ?v1=1 to any URL to view the historical (pre-v2) design. Remove or use ?v1=0 to come back.' },
      { title: 'Esc — Close modals & legends',
        content: 'Esc closes the active modal, the icon legend popover, and this Help Center.' },
      { title: 'Chat prefill via event',
        content: 'Anywhere in the app, dispatch window.dispatchEvent(new CustomEvent(\'risedualai-open-chat\', { detail: { prefill: "…", autoSend: true } })) to drop a prompt into AI Chat.' },
    ],
  },
  {
    key: 'faq', label: 'FAQ', icon: Info, accent: '#3DE8D9',
    desc: 'Common questions',
    items: [
      { title: 'Why is the Classic UI pill in my header?',
        content: 'It\'s a reversible switch. v2 is the current default; Classic lets you step back to the prior layout without losing any data. Both pull from the same APIs.' },
      { title: 'How do referral rewards work?',
        content: 'Share your Smart Money Board PNG — the QR encodes your ref code. 5 unique scans/month = +7 days Pro. Monthly leaderboard: #1 wins Pro Max for 30 days, #2 wins Pro for 30 days, #3–5 win 100 credits.' },
      { title: 'How do I connect a real broker?',
        content: 'Kraken is live for crypto. Alpaca stocks is in OAuth preview (the ?demo=oauth flow). IBKR & Schwab are wired but require your own API credentials. Admin → Broker OAuth.' },
      { title: 'Where are my Pro benefits?',
        content: 'Automatically unlocked on subscription. Check badge on your avatar. Pro Max adds unlimited War Room + unlimited AI Chat + 50K monthly credits.' },
      { title: 'Why do some predictions show "NEUTRAL" at high confidence?',
        content: 'A high-confidence NEUTRAL means the AI believes the market is genuinely sideways for the next window — not that it\'s unsure. The Auditor vetoes directional calls that don\'t survive stress-testing.' },
      { title: 'Daily digest email is empty',
        content: 'Fixed Feb 2026. If you\'re still seeing blank content, check Workspace → Credits to confirm your account is active, then Admin can re-trigger via POST /api/digest/trigger.' },
    ],
  },
];

/* ─────────────────────────────  Search utils  ───────────────────────────── */
function scoreMatch(text, q) {
  const t = (text || '').toLowerCase();
  if (!q || !t) return 0;
  if (t.includes(q)) return 5 + (t.startsWith(q) ? 3 : 0);
  // token match — any query token present
  const tokens = q.split(/\s+/).filter(Boolean);
  let hits = 0;
  for (const tok of tokens) if (t.includes(tok)) hits++;
  return hits / Math.max(1, tokens.length) > 0.5 ? 2 : 0;
}

function searchAll(query) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const out = [];
  for (const sec of SECTIONS) {
    for (const item of sec.items) {
      const s = scoreMatch(item.title, q) * 2 + scoreMatch(item.content, q);
      if (s > 0) out.push({ section: sec, item, score: s });
    }
  }
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, 30);
}

/* ───────────────────────────────  UI  ───────────────────────────────────── */
const HelpCenter = ({ onClose, initialSection, contextHub, onNavigate }) => {
  const { enabled: v2Nav } = useV2Nav();
  const [tab, setTab] = useState(initialSection || 'start');
  const [query, setQuery] = useState('');
  const results = useMemo(() => searchAll(query), [query]);
  const section = SECTIONS.find(s => s.key === tab) || SECTIONS[0];
  const telemetryTimer = useRef(null);

  // Esc to close
  useEffect(() => {
    const esc = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);

  // Debounced search telemetry — fires 700ms after the user stops typing
  useEffect(() => {
    if (telemetryTimer.current) clearTimeout(telemetryTimer.current);
    const q = query.trim();
    if (q.length < 2) return;
    telemetryTimer.current = setTimeout(() => {
      fetch(`${API}/analytics/help-search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          q,
          results_count: results.length,
          context_hub: contextHub || null,
        }),
      }).catch(() => { /* fire-and-forget */ });
    }, 700);
    return () => telemetryTimer.current && clearTimeout(telemetryTimer.current);
  }, [query, results.length, contextHub]);

  const go = (viewSubTab) => {
    if (!v2Nav || !onNavigate || !viewSubTab) return;
    onNavigate(viewSubTab[0], viewSubTab[1]);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-start sm:items-center justify-center p-2 sm:p-4 bg-black/70 backdrop-blur-sm overflow-y-auto" data-testid="help-center">
      <div className="w-full max-w-5xl my-4 bg-[#0b1426] border border-slate-700/60 rounded-2xl flex flex-col max-h-[95vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 sm:px-6 py-3 border-b border-slate-700/50 shrink-0">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-[#3DE8D9]/10 border border-[#3DE8D9]/30 flex items-center justify-center">
              <HelpCircle className="w-4 h-4 text-[#3DE8D9]" />
            </div>
            <h2 className="text-white font-bold text-base sm:text-lg">Help Center</h2>
            <span className="hidden sm:inline text-slate-500 text-[10px] tracking-wide uppercase ml-1">RISEDUAL AI v2</span>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white p-1 rounded" aria-label="Close Help Center" data-testid="help-close-btn">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search bar */}
        <div className="px-4 sm:px-6 pt-4 pb-3 shrink-0 border-b border-slate-800/70">
          <div className="relative">
            <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search help — try 'smart order', 'referral', 'tier 3'…"
              className="w-full bg-slate-900/70 border border-slate-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-[#3DE8D9]/50"
              data-testid="help-search-input"
              autoFocus
            />
            {query && (
              <button
                onClick={() => setQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white p-1"
                aria-label="Clear search"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          {query && (
            <div className="mt-2 text-[11px] text-slate-500">
              {results.length
                ? <>{results.length} match{results.length === 1 ? '' : 'es'}</>
                : <span className="text-orange-400/80">No matches — we track these to improve docs.</span>}
            </div>
          )}
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4">
          {query ? (
            /* Search results list */
            <div className="space-y-2">
              {results.length === 0 && (
                <div className="text-slate-400 text-sm py-10 text-center">
                  <Zap className="w-5 h-5 mx-auto mb-2 text-slate-600" />
                  <p>Nothing found for <span className="text-white">&ldquo;{query}&rdquo;</span>.</p>
                  <p className="text-[11px] mt-1 text-slate-500">Your search was logged — the admin panel tracks gaps.</p>
                </div>
              )}
              {results.map((r, i) => {
                const Icon = r.section.icon;
                return (
                  <div
                    key={i}
                    className="rounded-xl border border-slate-700/40 bg-slate-900/30 p-3 hover:border-slate-500/60 transition-colors"
                    data-testid={`help-result-${i}`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <Icon className="w-3.5 h-3.5 shrink-0" style={{ color: r.section.accent }} />
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{r.section.label}</span>
                    </div>
                    <h4 className="text-white text-sm font-semibold mb-1">{r.item.title}</h4>
                    <p className="text-slate-300 text-xs leading-relaxed">{r.item.content}</p>
                    {r.item.goto && v2Nav && (
                      <button
                        onClick={() => go(r.item.goto)}
                        className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-[#3DE8D9] hover:text-white transition-colors"
                        data-testid={`help-goto-${i}`}
                      >
                        Take me there <ArrowRight className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            /* Tabbed browse */
            <>
              <IconTabBar
                tabs={SECTIONS.map(s => ({ key: s.key, label: s.label, icon: s.icon, desc: s.desc }))}
                value={tab}
                onChange={setTab}
                enabled
                accent="text-[#3DE8D9]"
                accentHex="#3DE8D9"
                testIdPrefix="help-tab"
                legendTitle="Help Sections"
              />
              <div className="space-y-2">
                {section.items.map((item, i) => (
                  <div
                    key={`${section.key}-${i}`}
                    className="rounded-xl border border-slate-700/40 bg-slate-900/30 p-3 hover:border-slate-500/60 transition-colors"
                    data-testid={`help-item-${section.key}-${i}`}
                  >
                    <h4 className="text-white text-sm font-semibold mb-1">{item.title}</h4>
                    <p className="text-slate-300 text-xs leading-relaxed">{item.content}</p>
                    {item.goto && v2Nav && (
                      <button
                        onClick={() => go(item.goto)}
                        className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-[#3DE8D9] hover:text-white transition-colors"
                        data-testid={`help-goto-${section.key}-${i}`}
                      >
                        Take me there <ArrowRight className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Footer hint */}
        <div className="px-4 sm:px-6 py-2 border-t border-slate-800/70 text-[10px] text-slate-600 shrink-0 flex items-center justify-between">
          <span>Press <kbd className="px-1 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-400 font-mono">Esc</kbd> to close</span>
          <span className="flex items-center gap-1"><Sparkles className="w-3 h-3 text-[#3DE8D9]" /> Context-aware help</span>
        </div>
      </div>
    </div>
  );
};

export default HelpCenter;
