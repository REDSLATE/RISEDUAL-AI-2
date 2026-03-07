import React, { useState } from 'react';
import { Search, ChevronDown } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';

const Navbar = () => {
  const [searchValue, setSearchValue] = useState('');

  return (
    <nav className="bg-[#1a1a1b] border-b border-gray-800 px-6 py-3 sticky top-0 z-50">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-8">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-white rounded-sm flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="none" className="w-6 h-6">
                <path d="M13 10V3L4 14h7v7l9-11h-7z" fill="#1a1a1b" />
              </svg>
            </div>
            <span className="text-white font-semibold text-lg">RISEDUALAI</span>
          </div>

          {/* Search Bar */}
          <div className="relative w-80">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-500 w-4 h-4" />
            <Input
              type="text"
              placeholder="Search Symbol"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 bg-[#272729] border-gray-700 text-white placeholder-gray-500 focus:border-gray-600"
            />
          </div>
        </div>

        {/* Navigation Menu */}
        <div className="flex items-center gap-6">
          <button className="text-gray-300 hover:text-white flex items-center gap-1 text-sm transition-colors">
            Platform <ChevronDown className="w-4 h-4" />
          </button>
          <button className="text-gray-300 hover:text-white flex items-center gap-1 text-sm transition-colors">
            Strategies <ChevronDown className="w-4 h-4" />
          </button>
          <button className="text-gray-300 hover:text-white flex items-center gap-1 text-sm transition-colors">
            Education <ChevronDown className="w-4 h-4" />
          </button>
          <button className="text-gray-300 hover:text-white flex items-center gap-1 text-sm transition-colors">
            Resources <ChevronDown className="w-4 h-4" />
          </button>
          <Button variant="outline" className="bg-white text-black hover:bg-gray-200 border-0">
            Login
          </Button>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;