import React, { useState } from 'react';
import { Search, ChevronDown, Menu, X, User, LogOut, Briefcase, Crown, PieChart, Radio, BookOpen } from 'lucide-react';
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

const Navbar = ({ onLogin, onRegister, onSubscribe, onOpenAdmin, onOpenWorkspace, onOpenPortfolio, onOpenSignals, onOpenJournal }) => {
  const [searchValue, setSearchValue] = useState('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { user, logout, isPro } = useAuth();

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
    <nav className="bg-slate-900/80 backdrop-blur-xl border-b border-slate-700/50 px-3 sm:px-6 py-3 sticky top-0 z-40">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 sm:gap-8">
          <div className="flex items-center gap-2.5 cursor-pointer" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            <div className="w-11 h-11 rounded-lg bg-slate-800/80 border border-slate-600/40 flex items-center justify-center p-1" style={{boxShadow: '0 0 12px rgba(0,82,255,0.3)'}}>
              <img src="/logo-icon.png" alt="RISEDUAL AI" className="w-full h-full object-contain brightness-125" />
            </div>
            <span className="text-white font-bold text-xl tracking-tight" style={{fontFamily: 'Manrope, sans-serif'}}>RISEDUAL AI</span>
          </div>

          <form onSubmit={handleSearch} className="relative hidden lg:block">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input type="text" placeholder="Search Symbol" value={searchValue} onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#0052FF] rounded-xl" data-testid="search-input" />
          </form>
        </div>

        {/* Desktop Nav */}
        <div className="hidden lg:flex items-center gap-4">
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="platform-menu">
              Platform <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('ai-hypothesis')}>AI Hypothesis</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-radar')}>AI Options Radar</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-flow')}>Options Flow Screener</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('dark-pool')}>Dark Pool Trading</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('macro-dashboard')}>Macro Intelligence</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('crypto')}>Crypto Market</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="strategies-menu">
              Strategies <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('momentum')}>Momentum Close Strength</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('fast-movers')}>Fast Mover Calls</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('unusual-volume')}>Unusual Options Volume</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="education-menu">
              Education <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={openChat}>Ask RISEDUAL AI</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('market-prediction')}>Market Analysis</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-radar')}>Options Guide</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="resources-menu">
              Resources <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('company-research')}>Company Research</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('market-prediction')}>AI Market Predictions</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('macro-dashboard')}>Macro Intelligence</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('crypto')}>Crypto Data</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => window.open('https://risedual.ai', '_blank')}>About RISEDUAL AI</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <BrokerConnect />

          {!isPro && (
            <Button variant="outline" className="bg-[#0052FF] text-white hover:bg-[#2563EB] border-0 font-semibold rounded-xl"
              onClick={onSubscribe} data-testid="upgrade-btn">
              Upgrade Pro
            </Button>
          )}

          {user ? (
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger className="flex items-center gap-2 text-slate-300 hover:text-white outline-none" data-testid="user-menu">
                <div className="w-8 h-8 bg-[#0052FF] rounded-full flex items-center justify-center text-white text-sm font-bold">
                  {(user.name || user.email || '?')[0].toUpperCase()}
                </div>
              </DropdownMenuTrigger>
              <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60] min-w-[200px]">
                <div className="px-3 py-2 border-b border-slate-700">
                  <p className="text-white text-sm font-medium">{user.name || user.email}</p>
                  <p className="text-slate-400 text-xs">{user.email}</p>
                  <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded mt-1 inline-block ${isPro ? 'bg-[#0052FF]/20 text-[#0052FF]' : 'bg-slate-700 text-slate-400'}`}>
                    {isPro ? 'PRO' : 'FREE'}
                  </span>
                </div>
                {!isPro && <DropdownMenuItem className="text-[#0052FF] hover:bg-slate-700 cursor-pointer" onSelect={onSubscribe}>Upgrade to Pro</DropdownMenuItem>}
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
                {user.role === 'owner' && (
                  <DropdownMenuItem className="text-red-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenAdmin} data-testid="nav-admin-btn">
                    <Crown className="w-4 h-4 mr-2" /> Admin Panel
                  </DropdownMenuItem>
                )}
                <DropdownMenuItem className="text-red-400 hover:bg-slate-700 cursor-pointer" onSelect={logout}>
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
            <Button variant="outline" size="sm" className="bg-[#0052FF] text-white hover:bg-[#2563EB] border-0 rounded-xl text-xs px-3"
              onClick={onSubscribe} data-testid="mobile-upgrade-btn">
              Pro
            </Button>
          )}
          {user ? (
            <div className="w-7 h-7 bg-[#0052FF] rounded-full flex items-center justify-center text-white text-xs font-bold" onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
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
        <div className="lg:hidden mt-3 pb-3 border-t border-slate-700/50 pt-3 space-y-3" data-testid="mobile-menu">
          <form onSubmit={handleSearch} className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input type="text" placeholder="Search Symbol" value={searchValue} onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#0052FF] rounded-xl w-full" data-testid="mobile-search-input" />
          </form>
          <div className="grid grid-cols-2 gap-2">
            {[
              { label: 'AI Hypothesis', id: 'ai-hypothesis' },
              { label: 'Market Prediction', id: 'market-prediction' },
              { label: 'Company Research', id: 'company-research' },
              { label: 'Macro Intelligence', id: 'macro-dashboard' },
              { label: 'Options Radar', id: 'options-radar' },
              { label: 'Dark Pool', id: 'dark-pool' },
              { label: 'Crypto Market', id: 'crypto' },
              { label: 'Momentum', id: 'momentum' },
            ].map(item => (
              <button key={item.id} onClick={() => mobileNav(item.id)}
                className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-800/50 border border-slate-700/50 hover:bg-slate-700/60 active:bg-slate-600/50 transition-colors">
                {item.label}
              </button>
            ))}
          </div>
          <div className="flex gap-2 pt-1">
            {user ? (
              <>
                <div className="flex-1 text-sm text-slate-300 flex items-center gap-2 px-3">
                  <User className="w-4 h-4" /> {user.name || user.email}
                  <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${isPro ? 'bg-[#0052FF]/20 text-[#0052FF]' : 'bg-slate-700 text-slate-400'}`}>
                    {isPro ? 'PRO' : 'FREE'}
                  </span>
                </div>
                <Button variant="outline" size="sm" className="bg-[#0052FF]/20 text-[#0052FF] border-[#0052FF]/30 rounded-xl text-xs"
                  onClick={() => { onOpenWorkspace(); setMobileMenuOpen(false); }} data-testid="mobile-workspace-btn">
                  <Briefcase className="w-3 h-3 mr-1" /> Workspace
                </Button>
                <Button variant="outline" size="sm" className="bg-[#0052FF]/20 text-[#0052FF] border-[#0052FF]/30 rounded-xl text-xs"
                  onClick={() => { onOpenPortfolio(); setMobileMenuOpen(false); }} data-testid="mobile-portfolio-btn">
                  <PieChart className="w-3 h-3 mr-1" /> Portfolio
                </Button>
                <Button variant="outline" size="sm" className="bg-amber-900/30 text-amber-400 border-amber-800/50 rounded-xl text-xs"
                  onClick={() => { onOpenSignals(); setMobileMenuOpen(false); }} data-testid="mobile-signals-btn">
                  <Radio className="w-3 h-3 mr-1" /> Signals
                </Button>
                <Button variant="outline" size="sm" className="bg-indigo-900/30 text-indigo-400 border-indigo-800/50 rounded-xl text-xs"
                  onClick={() => { onOpenJournal(); setMobileMenuOpen(false); }} data-testid="mobile-journal-btn">
                  <BookOpen className="w-3 h-3 mr-1" /> Journal
                </Button>
                {user.role === 'owner' && (
                  <Button variant="outline" size="sm" className="bg-red-900/30 text-red-400 border-red-800/50 rounded-xl text-xs"
                    onClick={() => { onOpenAdmin(); setMobileMenuOpen(false); }} data-testid="mobile-admin-btn">
                    <Crown className="w-3 h-3 mr-1" /> Admin
                  </Button>
                )}
                <Button variant="outline" size="sm" className="bg-red-900/30 text-red-400 border-red-800/50 rounded-xl text-xs"
                  onClick={() => { logout(); setMobileMenuOpen(false); }}>
                  Log Out
                </Button>
              </>
            ) : (
              <>
                <Button className="flex-1 bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl text-sm" onClick={() => { onRegister(); setMobileMenuOpen(false); }}>
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
