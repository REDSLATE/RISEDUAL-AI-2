import React, { useState } from 'react';
import { Search, ChevronDown, Menu, X, User, LogOut, Briefcase, Crown, PieChart, Radio, BookOpen, Wand2, Store, Database, LineChart } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import BrokerConnect from './BrokerConnect';
import { useAuth } from '../contexts/AuthContext';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const scrollTo = (id) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
const openChat = () => window.dispatchEvent(new CustomEvent('risedualai-open-chat'));

const Navbar = ({ onLogin, onRegister, onSubscribe, onOpenAdmin, onOpenWorkspace, onOpenPortfolio, onOpenSignals, onOpenJournal, onOpenStrategy, onOpenMarketplace, onOpenMemory, onOpenPaperTrading, onOpenAbout }) => {
  const [searchValue, setSearchValue] = useState('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { user, logout, isPro } = useAuth();
  const searchRef = React.useRef(null);

  // Ctrl+K / Cmd+K keyboard shortcut for search
  React.useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const handleSearch = (e) => {
    e.preventDefault();
    if (searchValue.trim()) {
      window.dispatchEvent(new CustomEvent('risedualai-research', { detail: searchValue.trim().toUpperCase() }));
      scrollTo('company-research');
      setMobileMenuOpen(false);
    }
  };

  const mobileNav = (id) => { scrollTo(id); setMobileMenuOpen(false); };

  return (
    <nav className="bg-slate-900/80 backdrop-blur-xl border-b border-slate-400/25 px-3 sm:px-6 py-3 sticky top-0 z-40">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 sm:gap-8">
          <div className="flex items-center gap-2.5 cursor-pointer" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            <div className="w-[50px] h-[50px] rounded-lg bg-slate-800/80 border border-slate-600/40 flex items-center justify-center p-1" style={{boxShadow: '0 0 12px rgba(0,82,255,0.3)'}}>
              <img src="/logo-icon.png" alt="RISEDUAL AI" className="w-full h-full object-contain brightness-125" />
            </div>
            <span className="text-white font-bold text-[23px] tracking-wide" style={{fontFamily: 'Manrope, sans-serif'}}>RISEDUAL <span className="text-[#3DE8D9]">AI</span></span>
          </div>

          <form onSubmit={handleSearch} className="relative hidden lg:block">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input ref={searchRef} type="text" placeholder="Search Symbol" value={searchValue} onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 pr-16 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl" data-testid="search-input" />
            <kbd className="absolute right-3 top-1/2 -translate-y-1/2 hidden lg:inline-flex items-center gap-0.5 text-[10px] text-slate-400 bg-slate-700/60 border border-slate-600/50 rounded px-1.5 py-0.5 font-mono pointer-events-none">
              <span className="text-[9px]">&#8984;</span>K
            </kbd>
          </form>
        </div>

        {/* Desktop Nav */}
        <div className="hidden lg:flex items-center gap-4">
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="platform-menu">
              Platform <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#060E1F] border-slate-600 z-[100] shadow-2xl shadow-black/60">
              <DropdownMenuItem className="text-orange-400 hover:text-orange-300 hover:bg-slate-700 cursor-pointer font-medium" onSelect={() => scrollTo('ai-war-room')} data-testid="nav-warroom-btn">AI War Room</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('ai-hypothesis')}>AI Hypothesis</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-radar')}>AI Options Radar</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-flow')}>Options Flow Screener</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('dark-pool')}>Dark Pool Trading</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('macro-dashboard')}>Macro Intelligence</DropdownMenuItem>
              <DropdownMenuItem className="text-violet-300 hover:text-violet-300 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('ai-intelligence')} data-testid="nav-intelligence-btn">AI Intelligence Hub</DropdownMenuItem>
              <DropdownMenuItem className="text-orange-400 hover:text-orange-300 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('sector-heatmap')} data-testid="nav-sector-heatmap-btn">Sector Heatmap</DropdownMenuItem>
              <DropdownMenuItem className="text-lime-400 hover:text-lime-300 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('pnl-tracker')} data-testid="nav-pnl-tracker-btn">P&L Tracker</DropdownMenuItem>
              <DropdownMenuItem className="text-cyan-400 hover:text-cyan-300 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('order-flow')} data-testid="nav-orderflow-btn">Order Flow</DropdownMenuItem>
              <DropdownMenuItem className="text-yellow-300 hover:text-yellow-300 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('whale-radar')} data-testid="nav-whale-radar-btn">Whale Radar</DropdownMenuItem>
              <DropdownMenuItem className="text-[#3DE8D9] hover:text-blue-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenMemory} data-testid="nav-memory-btn">Memory Dashboard</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('crypto')}>Crypto Market</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="strategies-menu">
              Strategies <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#060E1F] border-slate-600 z-[100] shadow-2xl shadow-black/60">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('momentum')}>Momentum Close Strength</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('fast-movers')}>Fast Mover Calls</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('unusual-volume')}>Unusual Options Volume</DropdownMenuItem>
              <DropdownMenuItem className="text-cyan-400 hover:text-cyan-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenMarketplace} data-testid="nav-marketplace-btn">
                <Store className="w-4 h-4 mr-2" /> Strategy Marketplace
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="education-menu">
              Education <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#060E1F] border-slate-600 z-[100] shadow-2xl shadow-black/60">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={openChat}>Ask RISEDUAL AI</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('market-prediction')}>Market Analysis</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-radar')}>Options Guide</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="resources-menu">
              Resources <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#060E1F] border-slate-600 z-[100] shadow-2xl shadow-black/60">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('company-research')}>Company Research</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('market-prediction')}>AI Market Predictions</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('macro-dashboard')}>Macro Intelligence</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('crypto')}>Crypto Data</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={onOpenAbout} data-testid="nav-about-btn">About RISEDUAL AI</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <BrokerConnect />

          {!isPro && (
            <Button variant="outline" className="bg-[#3DE8D9] text-white hover:bg-[#7AEEE0] border-0 font-semibold rounded-xl"
              onClick={onSubscribe} data-testid="upgrade-btn">
              Upgrade Pro
            </Button>
          )}

          {user ? (
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger className="flex items-center gap-2 text-slate-300 hover:text-white outline-none" data-testid="user-menu">
                <div className="w-8 h-8 bg-[#3DE8D9] rounded-full flex items-center justify-center text-white text-sm font-bold">
                  {(user.name || user.email || '?')[0].toUpperCase()}
                </div>
              </DropdownMenuTrigger>
              <DropdownMenuContent className="bg-[#060E1F] border-slate-600 z-[100] shadow-2xl shadow-black/60 min-w-[200px]">
                <div className="px-3 py-2 border-b border-slate-400/30">
                  <p className="text-white text-sm font-medium">{user.name || user.email}</p>
                  <p className="text-slate-300 text-xs">{user.email}</p>
                  <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded mt-1 inline-block ${isPro ? 'bg-[#3DE8D9]/20 text-[#3DE8D9]' : 'bg-slate-700 text-slate-400'}`}>
                    {isPro ? 'PRO' : 'FREE'}
                  </span>
                </div>
                {!isPro && <DropdownMenuItem className="text-[#3DE8D9] hover:bg-slate-700 cursor-pointer" onSelect={onSubscribe}>Upgrade to Pro</DropdownMenuItem>}
                <DropdownMenuItem className="text-slate-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenWorkspace} data-testid="nav-workspace-btn">
                  <Briefcase className="w-4 h-4 mr-2" /> My Workspace
                </DropdownMenuItem>
                <DropdownMenuItem className="text-slate-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenPortfolio} data-testid="nav-portfolio-btn">
                  <PieChart className="w-4 h-4 mr-2" /> Portfolio Analyzer
                </DropdownMenuItem>
                <DropdownMenuItem className="text-slate-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenSignals} data-testid="nav-signals-btn">
                  <Radio className="w-4 h-4 mr-2" /> Market Signals
                </DropdownMenuItem>
                <DropdownMenuItem className="text-slate-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenJournal} data-testid="nav-journal-btn">
                  <BookOpen className="w-4 h-4 mr-2" /> Trading Journal
                </DropdownMenuItem>
                <DropdownMenuItem className="text-violet-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenStrategy} data-testid="nav-strategy-btn">
                  <Wand2 className="w-4 h-4 mr-2" /> Strategy Builder
                </DropdownMenuItem>
                <DropdownMenuItem className="text-cyan-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenMarketplace} data-testid="nav-marketplace-menu-btn">
                  <Store className="w-4 h-4 mr-2" /> Marketplace
                </DropdownMenuItem>
                <DropdownMenuItem className="text-[#3DE8D9] hover:bg-slate-700 cursor-pointer" onSelect={onOpenMemory} data-testid="nav-memory-menu-btn">
                  <Database className="w-4 h-4 mr-2" /> Memory Dashboard
                </DropdownMenuItem>
                <DropdownMenuItem className="text-lime-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenPaperTrading} data-testid="nav-paper-trading-btn">
                  <LineChart className="w-4 h-4 mr-2" /> Paper Trading
                </DropdownMenuItem>
                {user.role === 'owner' && (
                  <DropdownMenuItem className="text-orange-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenAdmin} data-testid="nav-admin-btn">
                    <Crown className="w-4 h-4 mr-2" /> Admin Panel
                  </DropdownMenuItem>
                )}
                <DropdownMenuItem className="text-orange-400 hover:bg-slate-700 cursor-pointer" onSelect={logout}>
                  <LogOut className="w-4 h-4 mr-2" /> Log Out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <Button variant="outline" className="bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl font-medium"
              onClick={onLogin} data-testid="login-btn">
              Login
            </Button>
          )}
        </div>

        {/* Mobile */}
        <div className="flex lg:hidden items-center gap-2">
          {!isPro && (
            <Button variant="outline" size="sm" className="bg-[#3DE8D9] text-white hover:bg-[#7AEEE0] border-0 rounded-xl text-xs px-3"
              onClick={onSubscribe} data-testid="mobile-upgrade-btn">
              Pro
            </Button>
          )}
          {user ? (
            <div className="w-7 h-7 bg-[#3DE8D9] rounded-full flex items-center justify-center text-white text-xs font-bold" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
              {(user.name || user.email || '?')[0].toUpperCase()}
            </div>
          ) : (
            <Button variant="outline" size="sm" className="bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl text-xs px-3"
              onClick={onLogin} data-testid="mobile-login-btn">
              Login
            </Button>
          )}
          <button onClick={() => setMobileMenuOpen(!mobileMenuOpen)} className="text-slate-300 hover:text-white p-2" data-testid="mobile-menu-btn">
            {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>
        </div>
      </div>

      {/* Mobile Menu */}
      {mobileMenuOpen && (
        <div className="lg:hidden mt-3 pb-3 border-t border-slate-400/25 pt-3 space-y-3" data-testid="mobile-menu">
          <form onSubmit={handleSearch} className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input type="text" placeholder="Search Symbol" value={searchValue} onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl w-full" data-testid="mobile-search-input" />
          </form>
          <div className="grid grid-cols-2 gap-2">
            {[
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
            ].map(item => (
              <button key={item.id} onClick={() => mobileNav(item.id)}
                className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25 hover:bg-slate-700/60 active:bg-slate-600/50 transition-colors">
                {item.label}
              </button>
            ))}
            <button onClick={() => { onOpenAbout(); setMobileMenuOpen(false); }}
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
                  onClick={() => { onOpenWorkspace(); setMobileMenuOpen(false); }} data-testid="mobile-workspace-btn">
                  <Briefcase className="w-3 h-3 mr-1" /> Workspace
                </Button>
                <Button variant="outline" size="sm" className="bg-[#3DE8D9]/20 text-[#3DE8D9] border-[#3DE8D9]/30 rounded-xl text-xs"
                  onClick={() => { onOpenPortfolio(); setMobileMenuOpen(false); }} data-testid="mobile-portfolio-btn">
                  <PieChart className="w-3 h-3 mr-1" /> Portfolio
                </Button>
                <Button variant="outline" size="sm" className="bg-amber-900/30 text-amber-300 border-amber-800/50 rounded-xl text-xs"
                  onClick={() => { onOpenSignals(); setMobileMenuOpen(false); }} data-testid="mobile-signals-btn">
                  <Radio className="w-3 h-3 mr-1" /> Signals
                </Button>
                <Button variant="outline" size="sm" className="bg-indigo-900/30 text-indigo-400 border-indigo-800/50 rounded-xl text-xs"
                  onClick={() => { onOpenJournal(); setMobileMenuOpen(false); }} data-testid="mobile-journal-btn">
                  <BookOpen className="w-3 h-3 mr-1" /> Journal
                </Button>
                <Button variant="outline" size="sm" className="bg-violet-900/30 text-violet-300 border-violet-800/50 rounded-xl text-xs"
                  onClick={() => { onOpenStrategy(); setMobileMenuOpen(false); }} data-testid="mobile-strategy-btn">
                  <Wand2 className="w-3 h-3 mr-1" /> Strategy
                </Button>
                <Button variant="outline" size="sm" className="bg-cyan-900/30 text-cyan-400 border-cyan-800/50 rounded-xl text-xs"
                  onClick={() => { onOpenMarketplace(); setMobileMenuOpen(false); }} data-testid="mobile-marketplace-btn">
                  <Store className="w-3 h-3 mr-1" /> Marketplace
                </Button>
                <Button variant="outline" size="sm" className="bg-blue-900/30 text-[#3DE8D9] border-blue-800/50 rounded-xl text-xs"
                  onClick={() => { onOpenMemory(); setMobileMenuOpen(false); }} data-testid="mobile-memory-btn">
                  <Database className="w-3 h-3 mr-1" /> Memory
                </Button>
                <Button variant="outline" size="sm" className="bg-lime-700 text-lime-400 border-lime-700/50 rounded-xl text-xs"
                  onClick={() => { onOpenPaperTrading(); setMobileMenuOpen(false); }} data-testid="mobile-paper-trading-btn">
                  <LineChart className="w-3 h-3 mr-1" /> Paper Trade
                </Button>
                {user.role === 'owner' && (
                  <Button variant="outline" size="sm" className="bg-orange-800 text-orange-400 border-orange-700/50 rounded-xl text-xs"
                    onClick={() => { onOpenAdmin(); setMobileMenuOpen(false); }} data-testid="mobile-admin-btn">
                    <Crown className="w-3 h-3 mr-1" /> Admin
                  </Button>
                )}
                <Button variant="outline" size="sm" className="bg-orange-800 text-orange-400 border-orange-700/50 rounded-xl text-xs"
                  onClick={() => { logout(); setMobileMenuOpen(false); }}>
                  Log Out
                </Button>
              </>
            ) : (
              <>
                <Button className="flex-1 bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl text-sm" onClick={() => { onRegister(); setMobileMenuOpen(false); }}>
                  Sign Up
                </Button>
                <Button variant="outline" className="flex-1 bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl text-sm"
                  onClick={() => { onLogin(); setMobileMenuOpen(false); }}>
                  Login
                </Button>
              </>
            )}
          </div>
        </div>
      )}
    </nav>
  );
};

export default Navbar;
