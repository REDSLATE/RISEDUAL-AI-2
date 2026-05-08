import React from 'react';
import {
  Search, User, LogOut, Briefcase, Crown, PieChart, Radio, BookOpen, Wand2, Store, Database,
  LineChart, Bot, TrendingUp, Globe, BarChart3, Swords, HelpCircle, Info, Building2,
} from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import UserBadge from './UserBadge';
import useV2Nav from '../hooks/useV2Nav';

/**
 * Slim navigation chip used inside the mobile menu. Compact, icon-led, one-tap.
 */
const NavChip = ({ icon: Icon, label, onClick, accent = 'teal', testId }) => {
  const accents = {
    teal:   'bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/25',
    orange: 'bg-orange-500/10 text-orange-300 border-orange-500/25',
    violet: 'bg-violet-500/10 text-violet-300 border-violet-500/25',
    amber:  'bg-amber-500/10 text-amber-300 border-amber-500/25',
    slate:  'bg-slate-800/60 text-slate-200 border-slate-600/40',
  };
  return (
    <button
      onClick={onClick}
      data-testid={testId}
      className={`flex items-center gap-2 text-left text-sm py-2.5 px-3 rounded-xl border font-medium ${accents[accent] || accents.slate}`}
    >
      <Icon className="w-4 h-4 shrink-0" />
      <span>{label}</span>
    </button>
  );
};

const MobileMenu = ({
  searchValue, setSearchValue, handleSearch, close,
  user, isPro, logout, onNavigate,
  onOpenAdmin, onOpenHelp, onOpenAbout, onLogin, onRegister,
  // legacy props — still supported when v2Nav is off
  onOpenWorkspace, onOpenPortfolio, onOpenSignals, onOpenJournal, onOpenStrategy,
  onOpenMarketplace, onOpenMemory, onOpenPaperTrading, onOpenSmartOrders, onOpenRiskCalc,
  onOpenScanner, onOpenBots,
}) => {
  const { enabled: v2Nav } = useV2Nav();
  const act = (fn) => { if (fn) fn(); close(); };
  const nav = (view, subTab) => { onNavigate(view, subTab); close(); };

  // ─────────────────────────────  V2 LAYOUT  ─────────────────────────────
  if (v2Nav) {
    return (
      <div className="space-y-4" data-testid="mobile-menu">
        {/* Search */}
        <form onSubmit={handleSearch} className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <Input
            type="text"
            placeholder="Search Symbol"
            value={searchValue}
            onChange={e => setSearchValue(e.target.value)}
            className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl w-full"
            data-testid="mobile-search-input"
          />
        </form>

        {/* Primary destinations — the 5 top-level hubs. No sub-tab duplicates;
            each hub has its own icon tab strip + legend once you open it. */}
        <div>
          <div className="flex items-center justify-between mb-2 px-1">
            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Navigate</span>
            <span className="text-[10px] text-orange-400 font-semibold">v2</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <NavChip icon={TrendingUp} label="Dashboard"  accent="teal"   onClick={() => nav('dashboard')}  testId="mobile-nav-dashboard" />
            <NavChip icon={Swords}     label="War Room"   accent="orange" onClick={() => nav('warroom')}    testId="mobile-nav-warroom" />
            <NavChip icon={Search}     label="Research"   accent="teal"   onClick={() => nav('research')}   testId="mobile-nav-research" />
            <NavChip icon={BarChart3}  label="Options"    accent="violet" onClick={() => nav('options')}    testId="mobile-nav-options" />
            <NavChip icon={Briefcase}  label="Workspace"  accent="amber"  onClick={() => nav('workspace')}  testId="mobile-nav-workspace" />
            <NavChip icon={Info}       label="About"      accent="slate"  onClick={() => act(onOpenAbout)}  testId="mobile-about-btn" />
          </div>
        </div>

        {/* Utility row — tiny */}
        {user && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                window.dispatchEvent(new CustomEvent('risedualai-open-broker-connect'));
                close();
              }}
              data-testid="mobile-broker-connect-btn"
              className="flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/30"
            >
              <Building2 className="w-3.5 h-3.5" /> Connect Broker
            </button>
            {(user.role === 'owner' || user.role === 'admin') && (
              <button
                onClick={() => act(onOpenAdmin)}
                data-testid="mobile-admin-btn"
                className="flex items-center justify-center px-3 py-2 rounded-xl text-xs font-semibold bg-orange-500/10 text-orange-300 border border-orange-500/25"
                aria-label="Admin"
              >
                <Crown className="w-3.5 h-3.5" />
              </button>
            )}
            {onOpenHelp && (
              <button
                onClick={() => act(onOpenHelp)}
                data-testid="mobile-help-btn"
                className="flex items-center justify-center w-10 h-10 rounded-xl text-slate-400 bg-slate-800/60 border border-slate-600/40"
                aria-label="Help"
              >
                <HelpCircle className="w-4 h-4" />
              </button>
            )}
          </div>
        )}

        {/* Account strip */}
        <div className="pt-2 border-t border-slate-800">
          {user ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 px-1 text-sm text-slate-300">
                <User className="w-4 h-4" />
                <span className="truncate flex-1">{user.name || user.email}</span>
                <UserBadge user={user} size="sm" />
              </div>
              <button
                onClick={() => { logout(); close(); }}
                className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-xs font-medium bg-slate-800 text-slate-400 border border-slate-600/30 hover:text-orange-400 hover:border-orange-700/30 transition-colors"
              >
                <LogOut className="w-3.5 h-3.5" /> Log Out
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <Button className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl text-sm" onClick={() => act(onRegister)}>
                Sign Up
              </Button>
              <Button variant="outline" className="bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl text-sm" onClick={() => act(onLogin)}>
                Login
              </Button>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ────────────────────────────  LEGACY LAYOUT  ────────────────────────────
  return (
    <div className="space-y-3" data-testid="mobile-menu">
      <form onSubmit={handleSearch} className="relative">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-4 h-4" />
        <Input type="text" placeholder="Search Symbol" value={searchValue} onChange={(e) => setSearchValue(e.target.value)}
          className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl w-full" data-testid="mobile-search-input" />
      </form>

      <div className="grid grid-cols-2 gap-2">
        <button onClick={() => nav('dashboard')} className="text-left text-white text-sm py-2.5 px-3 rounded-lg bg-[#3DE8D9]/10 border border-[#3DE8D9]/25 font-medium">
          <TrendingUp className="w-4 h-4 inline mr-1.5 text-[#3DE8D9]" />Dashboard
        </button>
        <button onClick={() => nav('research')} className="text-left text-white text-sm py-2.5 px-3 rounded-lg bg-[#3DE8D9]/10 border border-[#3DE8D9]/25 font-medium">
          <Search className="w-4 h-4 inline mr-1.5 text-[#3DE8D9]" />Research
        </button>
        <button onClick={() => nav('options')} className="text-left text-white text-sm py-2.5 px-3 rounded-lg bg-violet-500/10 border border-violet-400/25 font-medium">
          <BarChart3 className="w-4 h-4 inline mr-1.5 text-violet-400" />Options
        </button>
        <button onClick={() => nav('workspace')} className="text-left text-white text-sm py-2.5 px-3 rounded-lg bg-amber-500/10 border border-amber-400/25 font-medium">
          <Briefcase className="w-4 h-4 inline mr-1.5 text-amber-400" />Workspace
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <button onClick={() => nav('research', 'hypothesis')} className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25">AI Hypothesis</button>
        <button onClick={() => nav('research', 'prediction')} className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25">Market Prediction</button>
        <button onClick={() => nav('research', 'company')} className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25">Company Research</button>
        <button onClick={() => nav('research', 'macro')} className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25">Macro Intelligence</button>
        <button onClick={() => nav('options', 'radar')} className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25">Options Radar</button>
        <button onClick={() => nav('options', 'darkpool')} className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25">Dark Pool</button>
        <button onClick={() => act(onOpenAbout)} className="text-left text-slate-300 text-sm py-2.5 px-3 rounded-lg bg-slate-700/60 border border-slate-400/25" data-testid="mobile-about-btn">About Us</button>
      </div>

      <div className="space-y-3 pt-1">
        {user ? (
          <>
            <div className="text-sm text-slate-300 flex items-center gap-2 px-1">
              <User className="w-4 h-4" /> {user.name || user.email}
              <UserBadge user={user} size="sm" />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <button onClick={() => nav('workspace')} data-testid="mobile-workspace-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/20">
                <Briefcase className="w-3.5 h-3.5" /> Workspace
              </button>
              <button onClick={() => act(onOpenPortfolio)} data-testid="mobile-portfolio-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/20">
                <PieChart className="w-3.5 h-3.5" /> Portfolio
              </button>
              <button onClick={() => act(onOpenSignals)} data-testid="mobile-signals-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-amber-900/20 text-amber-300 border border-amber-800/30">
                <Radio className="w-3.5 h-3.5" /> Signals
              </button>
              <button onClick={() => act(onOpenJournal)} data-testid="mobile-journal-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-indigo-900/20 text-indigo-400 border border-indigo-800/30">
                <BookOpen className="w-3.5 h-3.5" /> Journal
              </button>
              <button onClick={() => act(onOpenStrategy)} data-testid="mobile-strategy-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-violet-900/20 text-violet-300 border border-violet-800/30">
                <Wand2 className="w-3.5 h-3.5" /> Strategy
              </button>
              <button onClick={() => act(onOpenMarketplace)} data-testid="mobile-marketplace-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-cyan-900/20 text-cyan-400 border border-cyan-800/30">
                <Store className="w-3.5 h-3.5" /> Market
              </button>
              <button onClick={() => act(onOpenMemory)} data-testid="mobile-memory-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-blue-900/20 text-[#3DE8D9] border border-blue-800/30">
                <Database className="w-3.5 h-3.5" /> Memory
              </button>
              <button onClick={() => act(onOpenPaperTrading)} data-testid="mobile-paper-trading-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-lime-900/20 text-lime-400 border border-lime-700/30">
                <LineChart className="w-3.5 h-3.5" /> Paper
              </button>
              <button onClick={() => act(onOpenBots)} data-testid="mobile-bots-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-amber-900/20 text-amber-400 border border-amber-700/30">
                <Bot className="w-3.5 h-3.5" /> Bots
              </button>
              {(user.role === 'owner' || user.role === 'admin') && (
                <button onClick={() => act(onOpenAdmin)} data-testid="mobile-admin-btn" className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium bg-orange-900/20 text-orange-400 border border-orange-700/30">
                  <Crown className="w-3.5 h-3.5" /> Admin
                </button>
              )}
            </div>
            <button onClick={() => { logout(); close(); }} className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-xs font-medium bg-slate-800 text-slate-400 border border-slate-600/30 hover:text-orange-400 hover:border-orange-700/30 transition-colors">
              <LogOut className="w-3.5 h-3.5" /> Log Out
            </button>
          </>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <Button className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl text-sm" onClick={() => act(onRegister)}>Sign Up</Button>
            <Button variant="outline" className="bg-white text-slate-900 hover:bg-slate-100 border-0 rounded-xl text-sm" onClick={() => act(onLogin)}>Login</Button>
          </div>
        )}
      </div>
    </div>
  );
};

export default MobileMenu;
