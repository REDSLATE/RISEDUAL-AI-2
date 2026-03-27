import React, { useState } from 'react';
import { Search, ChevronDown } from 'lucide-react';
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

const Navbar = () => {
  const [searchValue, setSearchValue] = useState('');
  const [showSubscription, setShowSubscription] = useState(false);

  const handleSearch = (e) => {
    e.preventDefault();
    if (searchValue.trim()) {
      // Scroll to relevant section or show results
      console.log('Searching for:', searchValue);
    }
  };

  return (
    <nav className="bg-slate-900/80 backdrop-blur-xl border-b border-slate-700/50 px-6 py-3 sticky top-0 z-40">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-8">
          {/* Logo */}
          <div className="flex items-center gap-2 cursor-pointer" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            <div className="w-8 h-8 bg-[#0052FF] rounded-lg flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5">
                <path d="M13 10V3L4 14h7v7l9-11h-7z" fill="white" />
              </svg>
            </div>
            <span className="text-white font-bold text-lg tracking-tight" style={{fontFamily: 'Manrope, sans-serif'}}>RISEDUALAI</span>
          </div>

          {/* Search Bar */}
          <form onSubmit={handleSearch} className="relative w-80">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-500 w-4 h-4" />
            <Input
              type="text"
              placeholder="Search Symbol"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#0052FF] rounded-xl"
            />
          </form>
        </div>

        {/* Navigation Menu */}
        <div className="flex items-center gap-6">
          <DropdownMenu>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none">
              Platform <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => document.getElementById('options-radar')?.scrollIntoView({ behavior: 'smooth' })}
              >
                AI Options Radar
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => document.getElementById('options-flow')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Options Flow Screener
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => document.getElementById('dark-pool')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Dark Pool Trading
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => document.getElementById('crypto')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Crypto Market
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none">
              Strategies <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => document.getElementById('momentum')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Momentum Close Strength
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => document.getElementById('fast-movers')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Fast Mover Calls
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => document.getElementById('unusual-volume')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Unusual Options Volume
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none">
              Education <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => document.getElementById('chat-window')?.classList.remove('hidden')}
              >
                Ask RISEDUALAI
              </DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-[#1E293B] cursor-pointer">
                Trading Basics
              </DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-[#1E293B] cursor-pointer">
                Options Guide
              </DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-[#1E293B] cursor-pointer">
                Risk Management
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none">
              Resources <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-slate-800 border-slate-700 z-[60]">
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-[#1E293B] cursor-pointer">
                API Documentation
              </DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-[#1E293B] cursor-pointer">
                Market Data Sources
              </DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-[#1E293B] cursor-pointer">
                Support Center
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer"
                onClick={() => window.open('https://emergent.sh', '_blank')}
              >
                About RISEDUALAI
              </DropdownMenuItem>
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
      </div>

      {showSubscription && (
        <SubscriptionPricing onClose={() => setShowSubscription(false)} />
      )}
    </nav>
  );
};

export default Navbar;