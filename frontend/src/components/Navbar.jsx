import React, { useState } from 'react';
import { Search, ChevronDown, Menu, X, User, LogOut, Briefcase, Crown, PieChart, Radio, BookOpen, Wand2, Store, Database, LineChart, Layers, Calculator, Radar, Bot, HelpCircle, Rocket, Code, AlertTriangle, Globe, CreditCard, Settings } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import BrokerConnect from './BrokerConnect';
import MobileMenu from './MobileMenu';
import UserBadge from './UserBadge';
import CreditBadge from './CreditBadge';
import { useAuth } from '../contexts/AuthContext';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const openChat = () => window.dispatchEvent(new CustomEvent('risedualai-open-chat'));

const NAV_DESTINATIONS = [
  { key: 'dashboard', label: 'Dashboard' },
  { key: 'research', label: 'Research' },
  { key: 'options', label: 'Options' },
  { key: 'workspace', label: 'Workspace' },
];

const Navbar = ({
  activeView, onNavigate,
  onLogin, onRegister, onSubscribe,
  onOpenAdmin, onOpenSignals, onOpenStrategy, onOpenMarketplace, onOpenMemory,
  onOpenHelp, onOpenDeveloper, onOpenCredits, onOpenSearchWarRoom,
  onStartTour, onOpenAbout,
  // Workspace modal triggers (passed through for user menu shortcuts)
  onOpenPortfolio, onOpenJournal, onOpenPaperTrading, onOpenSmartOrders,
  onOpenRiskCalc, onOpenScanner, onOpenBots, onOpenFailureLoop,
}) => {
  const [searchValue, setSearchValue] = useState('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { user, logout, isPro } = useAuth();
  const searchRef = React.useRef(null);

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
      onNavigate('research', 'company');
      setMobileMenuOpen(false);
    }
  };

  return (
    <nav className="bg-slate-900/80 backdrop-blur-xl border-b border-slate-400/25 px-3 sm:px-6 py-3 sticky top-0 z-40">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 sm:gap-6">
          {/* Logo */}
          <div className="flex items-center gap-2.5 cursor-pointer" onClick={() => onNavigate('dashboard')}>
            <div className="w-[50px] h-[50px] rounded-lg bg-slate-800/80 border border-slate-600/40 flex items-center justify-center p-1" style={{boxShadow: '0 0 12px rgba(0,82,255,0.3)'}}>
              <img src="/logo-icon.png" alt="RISEDUAL AI" className="w-full h-full object-contain brightness-125" />
            </div>
            <span className="text-white font-bold text-[23px] tracking-wide" style={{fontFamily: 'Manrope, sans-serif'}}>RISEDUAL <span className="text-[#3DE8D9]">AI</span></span>
          </div>

          {/* Primary destinations */}
          <div className="hidden lg:flex items-center gap-1">
            {NAV_DESTINATIONS.map(item => (
              <button
                key={item.key}
                onClick={() => onNavigate(item.key)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  activeView === item.key
                    ? 'bg-[#3DE8D9]/15 text-[#3DE8D9]'
                    : 'text-slate-300 hover:text-white hover:bg-slate-800/50'
                }`}
                data-testid={`nav-${item.key}`}
              >
                {item.label}
              </button>
            ))}
          </div>

          {/* Research sub-nav (visible when on Research view) */}
          {activeView === 'research' && (
            <div className="hidden lg:flex items-center gap-1 ml-2 pl-2 border-l border-slate-700/50">
              <button onClick={() => onNavigate('research', 'hypothesis')} className="text-[11px] text-slate-400 hover:text-[#3DE8D9] px-2 py-1 rounded transition-colors">AI Hypothesis</button>
              <button onClick={() => onNavigate('research', 'prediction')} className="text-[11px] text-slate-400 hover:text-[#3DE8D9] px-2 py-1 rounded transition-colors">Predictions</button>
              <button onClick={() => onNavigate('research', 'company')} className="text-[11px] text-slate-400 hover:text-[#3DE8D9] px-2 py-1 rounded transition-colors">Company</button>
              <button onClick={() => onNavigate('research', 'macro')} className="text-[11px] text-slate-400 hover:text-[#3DE8D9] px-2 py-1 rounded transition-colors">Macro</button>
            </div>
          )}

          {/* Options sub-nav */}
          {activeView === 'options' && (
            <div className="hidden lg:flex items-center gap-1 ml-2 pl-2 border-l border-slate-700/50">
              <button onClick={() => onNavigate('options', 'radar')} className="text-[11px] text-slate-400 hover:text-violet-400 px-2 py-1 rounded transition-colors">Radar</button>
              <button onClick={() => onNavigate('options', 'flow')} className="text-[11px] text-slate-400 hover:text-violet-400 px-2 py-1 rounded transition-colors">Flow</button>
              <button onClick={() => onNavigate('options', 'darkpool')} className="text-[11px] text-slate-400 hover:text-violet-400 px-2 py-1 rounded transition-colors">Dark Pool</button>
            </div>
          )}

          {/* Search */}
          <form onSubmit={handleSearch} className="relative hidden lg:block">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input ref={searchRef} type="text" placeholder="Search Symbol" value={searchValue} onChange={(e) => setSearchValue(e.target.value)}
              className="pl-10 pr-16 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl w-40" data-testid="search-input" />
            <kbd className="absolute right-3 top-1/2 -translate-y-1/2 hidden lg:inline-flex items-center gap-0.5 text-[10px] text-slate-400 bg-slate-700/60 border border-slate-600/50 rounded px-1.5 py-0.5 font-mono pointer-events-none">
              <span className="text-[9px]">&#8984;</span>K
            </kbd>
          </form>
        </div>

        {/* Desktop right */}
        <div className="hidden lg:flex items-center gap-3">
          {/* Tools dropdown — retains old Platform + Strategies + Resources color coding */}
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger className="text-slate-300 hover:text-slate-50 flex items-center gap-1 text-sm transition-colors outline-none" data-testid="tools-menu">
              Tools <ChevronDown className="w-4 h-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="bg-[#060E1F] border-slate-600 z-[100] shadow-2xl shadow-black/60">
              <DropdownMenuItem className="text-orange-400 hover:text-orange-300 hover:bg-slate-700 cursor-pointer font-medium" onSelect={() => onNavigate('dashboard')} data-testid="nav-warroom-btn">AI War Room</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={() => onNavigate('research', 'hypothesis')}>AI Hypothesis</DropdownMenuItem>
              <DropdownMenuItem className="text-violet-300 hover:text-violet-300 hover:bg-slate-700 cursor-pointer" onSelect={() => onNavigate('dashboard')} data-testid="nav-intelligence-btn">AI Intelligence Hub</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={onOpenSignals} data-testid="nav-signals-btn">
                <Radio className="w-4 h-4 mr-2" /> Market Signals
              </DropdownMenuItem>
              <DropdownMenuItem className="text-violet-300 hover:text-violet-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenStrategy} data-testid="nav-strategy-btn">
                <Wand2 className="w-4 h-4 mr-2" /> Strategy Builder
              </DropdownMenuItem>
              <DropdownMenuItem className="text-cyan-400 hover:text-cyan-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenMarketplace} data-testid="nav-marketplace-btn">
                <Store className="w-4 h-4 mr-2" /> Strategy Marketplace
              </DropdownMenuItem>
              <DropdownMenuItem className="text-sky-400 hover:text-sky-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenSearchWarRoom} data-testid="nav-search-war-room-btn">
                <Globe className="w-4 h-4 mr-2" /> Search War Room
              </DropdownMenuItem>
              <DropdownMenuItem className="text-[#3DE8D9] hover:text-blue-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenMemory} data-testid="nav-memory-btn">
                <Database className="w-4 h-4 mr-2" /> Memory Dashboard
              </DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={openChat}>Ask RISEDUAL AI</DropdownMenuItem>
              <DropdownMenuItem className="text-slate-300 hover:text-slate-50 hover:bg-slate-700 cursor-pointer" onSelect={onOpenAbout} data-testid="nav-about-btn">About RISEDUAL AI</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <BrokerConnect />

          {user && (
            <div className="flex items-center gap-1">
              <CreditBadge onClick={onOpenCredits} />
              <button onClick={() => onNavigate('workspace')} className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800/60 border border-slate-600/30 text-amber-400 hover:bg-slate-700 transition-colors" data-testid="nav-bots-shortcut">
                <Bot className="w-3.5 h-3.5" />
                <span className="text-[10px] font-semibold hidden lg:inline">Bots</span>
              </button>
              <button onClick={onOpenHelp} className="flex items-center justify-center w-8 h-8 rounded-lg text-slate-400 hover:text-[#3DE8D9] hover:bg-slate-800/60 transition-colors" data-testid="nav-help-btn">
                <HelpCircle className="w-4 h-4" />
              </button>
              {onStartTour && (
                <button onClick={onStartTour} className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-slate-400 hover:text-[#3DE8D9] hover:bg-slate-800/60 transition-colors text-[10px]" data-testid="nav-tour-btn">
                  <Rocket className="w-3 h-3" /> Tour
                </button>
              )}
            </div>
          )}

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
                  <div className="mt-1 flex items-center gap-1.5">
                    <UserBadge user={user} size="sm" />
                    {isPro && (user.role !== 'owner' && user.role !== 'admin') && (
                      <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded bg-[#3DE8D9]/20 text-[#3DE8D9]">PRO</span>
                    )}
                  </div>
                </div>
                {!isPro && <DropdownMenuItem className="text-[#3DE8D9] hover:bg-slate-700 cursor-pointer" onSelect={onSubscribe}>Upgrade to Pro</DropdownMenuItem>}
                <DropdownMenuItem className="text-slate-300 hover:bg-slate-700 cursor-pointer" onSelect={() => onNavigate('workspace')} data-testid="nav-workspace-btn">
                  <Briefcase className="w-4 h-4 mr-2" /> My Workspace
                </DropdownMenuItem>
                <DropdownMenuItem className="text-slate-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenPortfolio} data-testid="nav-portfolio-btn">
                  <PieChart className="w-4 h-4 mr-2" /> Portfolio Analyzer
                </DropdownMenuItem>
                <DropdownMenuItem className="text-slate-300 hover:bg-slate-700 cursor-pointer" onSelect={onOpenJournal} data-testid="nav-journal-btn">
                  <BookOpen className="w-4 h-4 mr-2" /> Trading Journal
                </DropdownMenuItem>
                <DropdownMenuItem className="text-lime-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenPaperTrading} data-testid="nav-paper-trading-btn">
                  <LineChart className="w-4 h-4 mr-2" /> Paper Trading
                </DropdownMenuItem>
                <DropdownMenuItem className="text-[#3DE8D9] hover:bg-slate-700 cursor-pointer" onSelect={onOpenSmartOrders} data-testid="nav-smart-orders-btn">
                  <Layers className="w-4 h-4 mr-2" /> Smart Orders
                </DropdownMenuItem>
                <DropdownMenuItem className="text-[#3DE8D9] hover:bg-slate-700 cursor-pointer" onSelect={onOpenRiskCalc} data-testid="nav-risk-calc-btn">
                  <Calculator className="w-4 h-4 mr-2" /> Risk Calculator
                </DropdownMenuItem>
                <DropdownMenuItem className="text-violet-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenScanner} data-testid="nav-scanner-btn">
                  <Radar className="w-4 h-4 mr-2" /> Market Scanner
                </DropdownMenuItem>
                <DropdownMenuItem className="text-amber-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenBots} data-testid="nav-bots-btn">
                  <Bot className="w-4 h-4 mr-2" /> Trading Bots
                </DropdownMenuItem>
                <DropdownMenuItem className="text-violet-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenDeveloper} data-testid="nav-developer-btn">
                  <Code className="w-4 h-4 mr-2" /> Developer API
                </DropdownMenuItem>
                <DropdownMenuItem className="text-red-400 hover:bg-slate-700 cursor-pointer" onSelect={onOpenFailureLoop} data-testid="nav-failure-loop-btn">
                  <AlertTriangle className="w-4 h-4 mr-2" /> Failure Loop
                </DropdownMenuItem>
                {(user.role === 'owner' || user.role === 'admin') && (
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
        <MobileMenu
          searchValue={searchValue} setSearchValue={setSearchValue}
          handleSearch={handleSearch}
          onNavigate={onNavigate}
          close={() => setMobileMenuOpen(false)}
          user={user} isPro={isPro} logout={logout}
          onOpenAdmin={onOpenAdmin} onOpenWorkspace={() => { onNavigate('workspace'); setMobileMenuOpen(false); }}
          onOpenPortfolio={onOpenPortfolio} onOpenSignals={onOpenSignals}
          onOpenJournal={onOpenJournal} onOpenStrategy={onOpenStrategy}
          onOpenMarketplace={onOpenMarketplace} onOpenMemory={onOpenMemory}
          onOpenPaperTrading={onOpenPaperTrading} onOpenSmartOrders={onOpenSmartOrders} onOpenRiskCalc={onOpenRiskCalc} onOpenScanner={onOpenScanner} onOpenBots={onOpenBots} onOpenHelp={onOpenHelp} onOpenAbout={onOpenAbout}
          onLogin={onLogin} onRegister={onRegister}
        />
      )}
    </nav>
  );
};

export default Navbar;
