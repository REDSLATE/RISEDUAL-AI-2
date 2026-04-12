import React from 'react';
import { Search, User, LogOut, Briefcase, Crown, PieChart, Radio, BookOpen, Wand2, Store, Database, LineChart } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';

const MOBILE_NAV_ITEMS = [
  { label: 'AI War Room', id: 'ai-war-room' },
  { label: 'AI Hypothesis', id: 'ai-hypothesis' },
  { label: 'Market Prediction', id: 'market-prediction' },
  { label: 'Company Research', id: 'company-research' },
  { label: 'Macro Intelligence', id: 'macro-dashboard' },
  { label: 'Sector Heatmap', id: 'sector-heatmap' },
  { label: 'P&L Tracker', id: 'pnl-tracker' },
  { label: 'Order Flow', id: 'order-flow' },
  { label: 'Whale Radar', id: 'whale-radar' },
  { label: 'Options Radar', id: 'options-radar' },
  { label: 'Dark Pool', id: 'dark-pool' },
  { label: 'Crypto Market', id: 'crypto' },
];

const MobileMenu = ({
  searchValue, setSearchValue, handleSearch, mobileNav, close,
  user, isPro, logout,
  onOpenAdmin, onOpenWorkspace, onOpenPortfolio, onOpenSignals,
  onOpenJournal, onOpenStrategy, onOpenMarketplace, onOpenMemory,
  onOpenPaperTrading, onOpenAbout, onLogin, onRegister,
}) => {
  const act = (fn) => { fn(); close(); };

  return (
    <div className="lg:hidden mt-3 pb-20 border-t border-slate-400/25 pt-3 space-y-3" data-testid="mobile-menu">
      <form onSubmit={handleSearch} className="relative">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
        <Input type="text" placeholder="Search Symbol" value={searchValue} onChange={(e) => setSearchValue(e.target.value)}
          className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl w-full" data-testid="mobile-search-input" />
      </form>
      <div className="grid grid-cols-2 gap-2">
        {MOBILE_NAV_ITEMS.map(item => (
          <button key={item.id} onClick={() => mobileNav(item.id)}
            className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25 hover:bg-slate-700/60 active:bg-slate-600/50 transition-colors">
            {item.label}
          </button>
        ))}
        <button onClick={() => act(onOpenAbout)}
          className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25 hover:bg-slate-700/60 active:bg-slate-600/50 transition-colors" data-testid="mobile-about-btn">
          About Us
        </button>
      </div>
      <div className="space-y-3 pt-1">
        {user ? (
          <>
            <div className="text-sm text-slate-300 flex items-center gap-2 px-1">
              <User className="w-4 h-4" /> {user.name || user.email}
              <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${isPro ? 'bg-[#3DE8D9]/20 text-[#3DE8D9]' : 'bg-slate-700 text-slate-400'}`}>
                {isPro ? 'PRO' : 'FREE'}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <button onClick={() => act(onOpenWorkspace)} data-testid="mobile-workspace-btn"
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/20">
                <Briefcase className="w-3.5 h-3.5" /> Workspace
              </button>
              <button onClick={() => act(onOpenPortfolio)} data-testid="mobile-portfolio-btn"
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/20">
                <PieChart className="w-3.5 h-3.5" /> Portfolio
              </button>
              <button onClick={() => act(onOpenSignals)} data-testid="mobile-signals-btn"
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-amber-900/20 text-amber-300 border border-amber-800/30">
                <Radio className="w-3.5 h-3.5" /> Signals
              </button>
              <button onClick={() => act(onOpenJournal)} data-testid="mobile-journal-btn"
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-indigo-900/20 text-indigo-400 border border-indigo-800/30">
                <BookOpen className="w-3.5 h-3.5" /> Journal
              </button>
              <button onClick={() => act(onOpenStrategy)} data-testid="mobile-strategy-btn"
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-violet-900/20 text-violet-300 border border-violet-800/30">
                <Wand2 className="w-3.5 h-3.5" /> Strategy
              </button>
              <button onClick={() => act(onOpenMarketplace)} data-testid="mobile-marketplace-btn"
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-cyan-900/20 text-cyan-400 border border-cyan-800/30">
                <Store className="w-3.5 h-3.5" /> Market
              </button>
              <button onClick={() => act(onOpenMemory)} data-testid="mobile-memory-btn"
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-blue-900/20 text-[#3DE8D9] border border-blue-800/30">
                <Database className="w-3.5 h-3.5" /> Memory
              </button>
              <button onClick={() => act(onOpenPaperTrading)} data-testid="mobile-paper-trading-btn"
                className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-lime-900/20 text-lime-400 border border-lime-700/30">
                <LineChart className="w-3.5 h-3.5" /> Paper
              </button>
              {(user.role === 'owner' || user.role === 'admin') && (
                <button onClick={() => act(onOpenAdmin)} data-testid="mobile-admin-btn"
                  className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-orange-900/20 text-orange-400 border border-orange-700/30">
                  <Crown className="w-3.5 h-3.5" /> Admin
                </button>
              )}
            </div>
            <button onClick={() => { logout(); close(); }}
              className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-xs font-medium bg-slate-800 text-slate-400 border border-slate-600/30 hover:text-orange-400 hover:border-orange-700/30 transition-colors">
              <LogOut className="w-3.5 h-3.5" /> Log Out
            </button>
          </>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <Button className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl text-sm" onClick={() => act(onRegister)}>
              Sign Up
            </Button>
            <Button variant="outline" className="bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl text-sm"
              onClick={() => act(onLogin)}>
              Login
            </Button>
          </div>
        )}
      </div>
    </div>
  );
};

export default MobileMenu;
