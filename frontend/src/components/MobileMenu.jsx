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
    <div className="lg:hidden mt-3 pb-3 border-t border-slate-400/25 pt-3 space-y-3" data-testid="mobile-menu">
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
      <div className="flex flex-wrap gap-2 pt-1">
        {user ? (
          <>
            <div className="w-full text-sm text-slate-300 flex items-center gap-2 px-1 mb-1">
              <User className="w-4 h-4" /> {user.name || user.email}
              <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${isPro ? 'bg-[#3DE8D9]/20 text-[#3DE8D9]' : 'bg-slate-700 text-slate-400'}`}>
                {isPro ? 'PRO' : 'FREE'}
              </span>
            </div>
            <Button variant="outline" size="sm" className="bg-[#3DE8D9]/20 text-[#3DE8D9] border-[#3DE8D9]/30 rounded-xl text-xs"
              onClick={() => act(onOpenWorkspace)} data-testid="mobile-workspace-btn">
              <Briefcase className="w-3 h-3 mr-1" /> Workspace
            </Button>
            <Button variant="outline" size="sm" className="bg-[#3DE8D9]/20 text-[#3DE8D9] border-[#3DE8D9]/30 rounded-xl text-xs"
              onClick={() => act(onOpenPortfolio)} data-testid="mobile-portfolio-btn">
              <PieChart className="w-3 h-3 mr-1" /> Portfolio
            </Button>
            <Button variant="outline" size="sm" className="bg-amber-900/30 text-amber-300 border-amber-800/50 rounded-xl text-xs"
              onClick={() => act(onOpenSignals)} data-testid="mobile-signals-btn">
              <Radio className="w-3 h-3 mr-1" /> Signals
            </Button>
            <Button variant="outline" size="sm" className="bg-indigo-900/30 text-indigo-400 border-indigo-800/50 rounded-xl text-xs"
              onClick={() => act(onOpenJournal)} data-testid="mobile-journal-btn">
              <BookOpen className="w-3 h-3 mr-1" /> Journal
            </Button>
            <Button variant="outline" size="sm" className="bg-violet-900/30 text-violet-300 border-violet-800/50 rounded-xl text-xs"
              onClick={() => act(onOpenStrategy)} data-testid="mobile-strategy-btn">
              <Wand2 className="w-3 h-3 mr-1" /> Strategy
            </Button>
            <Button variant="outline" size="sm" className="bg-cyan-900/30 text-cyan-400 border-cyan-800/50 rounded-xl text-xs"
              onClick={() => act(onOpenMarketplace)} data-testid="mobile-marketplace-btn">
              <Store className="w-3 h-3 mr-1" /> Marketplace
            </Button>
            <Button variant="outline" size="sm" className="bg-blue-900/30 text-[#3DE8D9] border-blue-800/50 rounded-xl text-xs"
              onClick={() => act(onOpenMemory)} data-testid="mobile-memory-btn">
              <Database className="w-3 h-3 mr-1" /> Memory
            </Button>
            <Button variant="outline" size="sm" className="bg-lime-700 text-lime-400 border-lime-700/50 rounded-xl text-xs"
              onClick={() => act(onOpenPaperTrading)} data-testid="mobile-paper-trading-btn">
              <LineChart className="w-3 h-3 mr-1" /> Paper Trade
            </Button>
            {(user.role === 'owner' || user.role === 'admin') && (
              <Button variant="outline" size="sm" className="bg-orange-800 text-orange-400 border-orange-700/50 rounded-xl text-xs"
                onClick={() => act(onOpenAdmin)} data-testid="mobile-admin-btn">
                <Crown className="w-3 h-3 mr-1" /> Admin
              </Button>
            )}
            <Button variant="outline" size="sm" className="bg-orange-800 text-orange-400 border-orange-700/50 rounded-xl text-xs"
              onClick={() => { logout(); close(); }}>
              Log Out
            </Button>
          </>
        ) : (
          <>
            <Button className="flex-1 bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl text-sm" onClick={() => act(onRegister)}>
              Sign Up
            </Button>
            <Button variant="outline" className="flex-1 bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl text-sm"
              onClick={() => act(onLogin)}>
              Login
            </Button>
          </>
        )}
      </div>
    </div>
  );
};

export default MobileMenu;
