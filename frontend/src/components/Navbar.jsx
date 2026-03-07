import React, { useState } from 'react';
import { Search, ChevronDown } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const Navbar = () => {
  const [searchValue, setSearchValue] = useState('');

  const handleSearch = (e) => {
    e.preventDefault();
    if (searchValue.trim()) {
      // Scroll to relevant section or show results
      console.log('Searching for:', searchValue);
    }
  };

  return (
    <nav className="bg-[#1a1a1b] border-b border-gray-800 px-6 py-3 sticky top-0 z-50">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-8">
          {/* Logo */}
          <div className="flex items-center gap-2 cursor-pointer" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            <div className="w-8 h-8 bg-white rounded-sm flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="none" className="w-6 h-6">
                <path d="M13 10V3L4 14h7v7l9-11h-7z" fill="#1a1a1b" />
              </svg>
            </div>
            <span className="text-white font-semibold text-lg">RISEDUALAI</span>
          </div>

          {/* Search Bar */}
          <form onSubmit={handleSearch} className="relative w-80">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-500 w-4 h-4" />
            <Input
              type="text"
              placeholder="Search Symbol"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 bg-[#272729] border-gray-700 text-white placeholder-gray-500 focus:border-gray-600"
            />
          </form>
        </div>

        {/* Navigation Menu */}
        <div className="flex items-center gap-6">
          <DropdownMenu>
            <DropdownMenuTrigger className="text-gray-300 hover:text-white flex items-center gap-1 text-sm transition-colors outline-none">
              Platform <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#1a1a1b] border-gray-800">
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => document.getElementById('options-radar')?.scrollIntoView({ behavior: 'smooth' })}
              >
                AI Options Radar
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => document.getElementById('options-flow')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Options Flow Screener
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => document.getElementById('dark-pool')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Dark Pool Trading
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => document.getElementById('crypto')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Crypto Market
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger className="text-gray-300 hover:text-white flex items-center gap-1 text-sm transition-colors outline-none">
              Strategies <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#1a1a1b] border-gray-800">
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => document.getElementById('momentum')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Momentum Close Strength
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => document.getElementById('fast-movers')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Fast Mover Calls
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => document.getElementById('unusual-volume')?.scrollIntoView({ behavior: 'smooth' })}
              >
                Unusual Options Volume
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger className="text-gray-300 hover:text-white flex items-center gap-1 text-sm transition-colors outline-none">
              Education <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#1a1a1b] border-gray-800">
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => document.getElementById('chat-window')?.classList.remove('hidden')}
              >
                Ask RISEDUALAI
              </DropdownMenuItem>
              <DropdownMenuItem className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer">
                Trading Basics
              </DropdownMenuItem>
              <DropdownMenuItem className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer">
                Options Guide
              </DropdownMenuItem>
              <DropdownMenuItem className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer">
                Risk Management
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger className="text-gray-300 hover:text-white flex items-center gap-1 text-sm transition-colors outline-none">
              Resources <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#1a1a1b] border-gray-800">
              <DropdownMenuItem className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer">
                API Documentation
              </DropdownMenuItem>
              <DropdownMenuItem className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer">
                Market Data Sources
              </DropdownMenuItem>
              <DropdownMenuItem className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer">
                Support Center
              </DropdownMenuItem>
              <DropdownMenuItem 
                className="text-gray-300 hover:text-white hover:bg-[#272729] cursor-pointer"
                onClick={() => window.open('https://emergent.sh', '_blank')}
              >
                About RISEDUALAI
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <Button variant="outline" className="bg-white text-black hover:bg-gray-200 border-0">
            Login
          </Button>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;