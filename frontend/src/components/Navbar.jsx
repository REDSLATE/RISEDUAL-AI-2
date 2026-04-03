import React, { useState } from 'react';
import { Search, ChevronDown, Menu, X } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import BrokerConnect from './BrokerConnect';
import SubscriptionPricing from './SubscriptionPricing';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const scrollTo = (id) => {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
};

const openChat = () => {
  window.dispatchEvent(new CustomEvent('risedualai-open-chat'));
};

const Navbar = () => {
  const [searchValue, setSearchValue] = useState('');
  const [showSubscription, setShowSubscription] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const handleSearch = (e) => {
    e.preventDefault();
    if (searchValue.trim()) {
      window.dispatchEvent(new CustomEvent('risedualai-research', { detail: searchValue.trim().toUpperCase() }));
      scrollTo('company-research');
      setMobileMenuOpen(false);
    }
  };

  const mobileNav = (id) => {
    scrollTo(id);
    setMobileMenuOpen(false);
  };

  return (
    <>
    <nav className="bg-slate-900/80 backdrop-blur-xl border-b border-slate-700/50 px-3 sm:px-6 py-3 sticky top-0 z-40">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 sm:gap-8">
          {/* Logo */}
          <div className="flex items-center gap-2 cursor-pointer" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            <div className="w-8 h-8 bg-[#0052FF] rounded-lg flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5">
                <path d="M13 10V3L4 14h7v7l9-11h-7z" fill="white" />
              </svg>
            </div>
            <span className="text-white font-bold text-lg tracking-tight" style={{fontFamily: 'Manrope, sans-serif'}}>RISEDUALAI</span>
          </div>

          {/* Desktop Search */}
          <form onSubmit={handleSearch} className="relative hidden lg:block">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input
              type="text"
              placeholder="Search Symbol"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#0052FF] rounded-xl"
              data-testid="search-input"
            />
          </form>
        </div>

        {/* Desktop Nav */}
        <div className="hidden lg:flex items-center gap-4">
          {/* Platform Dropdown */}
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="platform-menu">
              Platform <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-radar')}>AI Options Radar</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-flow')}>Options Flow Screener</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('dark-pool')}>Dark Pool Trading</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('macro-dashboard')}>Macro Intelligence</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('crypto')}>Crypto Market</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          {/* Strategies Dropdown */}
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

          {/* Education Dropdown */}
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="education-menu">
              Education <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={openChat}>Ask RISEDUALAI</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('market-prediction')}>Market Analysis</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('options-radar')}>Options Guide</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('dark-pool')}>Dark Pool Explained</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          {/* Resources Dropdown */}
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="resources-menu">
              Resources <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('company-research')}>Company Research</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('market-prediction')}>AI Market Predictions</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('macro-dashboard')}>Macro Intelligence</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => scrollTo('crypto')}>Crypto Data</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={openChat}>Support / Ask AI</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => window.open('https://risedual.ai', '_blank')}>About RISEDUALAI</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <BrokerConnect />

          <Button 
            variant="outline" 
            className="bg-[#0052FF] text-white hover:bg-[#2563EB] border-0 font-semibold rounded-xl"
            onClick={() => setShowSubscription(true)}
            data-testid="upgrade-btn"
          >
            Upgrade Pro
          </Button>

          <Button 
            variant="outline" 
            className="bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl font-medium"
            onClick={() => alert('Login functionality coming soon! This will integrate with authentication.')}
          >
            Login
          </Button>
        </div>

        {/* Mobile: Search + Hamburger */}
        <div className="flex lg:hidden items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="bg-[#0052FF] text-white hover:bg-[#2563EB] border-0 rounded-xl text-xs px-3"
            onClick={() => setShowSubscription(true)}
            data-testid="mobile-upgrade-btn"
          >
            Pro
          </Button>
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="text-slate-300 hover:text-white p-2"
            data-testid="mobile-menu-btn"
          >
            {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>
        </div>
      </div>

      {/* Mobile Slide-down Menu */}
      {mobileMenuOpen && (
        <div className="lg:hidden mt-3 pb-3 border-t border-slate-700/50 pt-3 space-y-3 animate-in slide-in-from-top" data-testid="mobile-menu">
          {/* Mobile Search */}
          <form onSubmit={handleSearch} className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input
              type="text"
              placeholder="Search Symbol"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#0052FF] rounded-xl w-full"
              data-testid="mobile-search-input"
            />
          </form>

          {/* Mobile Nav Links */}
          <div className="grid grid-cols-2 gap-2">
            {[
              { label: 'Market Prediction', id: 'market-prediction' },
              { label: 'Company Research', id: 'company-research' },
              { label: 'Macro Intelligence', id: 'macro-dashboard' },
              { label: 'Options Radar', id: 'options-radar' },
              { label: 'Options Flow', id: 'options-flow' },
              { label: 'Dark Pool', id: 'dark-pool' },
              { label: 'Crypto Market', id: 'crypto' },
              { label: 'Momentum', id: 'momentum' },
            ].map(item => (
              <button
                key={item.id}
                onClick={() => mobileNav(item.id)}
                className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-800/50 border border-slate-700/50 hover:bg-slate-700/60 active:bg-slate-600/50 transition-colors"
              >
                {item.label}
              </button>
            ))}
          </div>

          {/* Mobile Actions */}
          <div className="flex gap-2 pt-1">
            <Button
              className="flex-1 bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl text-sm"
              onClick={() => { setShowSubscription(true); setMobileMenuOpen(false); }}
            >
              Upgrade Pro
            </Button>
            <Button
              variant="outline"
              className="flex-1 bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl text-sm"
              onClick={() => alert('Login functionality coming soon!')}
            >
              Login
            </Button>
          </div>
        </div>
      )}
    </nav>
    {showSubscription && (
      <SubscriptionPricing onClose={() => setShowSubscription(false)} />
    )}
    </>
  );
};

export default Navbar;
