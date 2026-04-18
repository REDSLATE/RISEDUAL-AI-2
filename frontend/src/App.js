import React from 'react';
import './App.css';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import useReferralCapture from './hooks/useReferralCapture';
import { Toaster } from './components/ui/sonner';
import ErrorBoundary from './components/ErrorBoundary';
import ScrollToTop from './components/ScrollToTop';
import Footer from './components/Footer';
import Navbar from './components/Navbar';
import StockTicker from './components/StockTicker';
import CryptoTicker from './components/CryptoTicker';
import Watchlist from './components/Watchlist';
import AlertsPanel from './components/AlertsPanel';
import RiseDualGPTChat from './components/RiseDualGPTChat';
import MobileBottomNav from './components/MobileBottomNav';
import AIWarRoom from './components/AIWarRoom';
import AIIntelligence from './components/AIIntelligence';
import SectorHeatmap from './components/SectorHeatmap';
import FearGreedGauge from './components/FearGreedGauge';
import LiveInsightsFeed from './components/LiveInsightsFeed';
import OrderFlowPanel from './components/OrderFlowPanel';
import WhaleRadar from './components/WhaleRadar';
import CryptoSection from './components/CryptoSection';
import AdditionalSections from './components/AdditionalSections';
import PromoBanner from './components/PromoBanner';
import AuthModal from './components/AuthModal';
import WaitlistModal from './components/WaitlistModal';
import ResetPasswordModal from './components/ResetPasswordModal';
import LandingPage from './components/LandingPage';
import LegalPages from './components/LegalPages';
import ModalManager from './components/ModalManager';
import OnboardingTour, { STORAGE_KEY as TOUR_KEY } from './components/OnboardingTour';
import useModals from './hooks/useModals';

const AlpacaOAuthDemo = React.lazy(() => import('./components/AlpacaOAuthDemo'));
const LiveDemoOverlay = React.lazy(() => import('./components/LiveDemoOverlay'));

// Hub pages
import ResearchHub from './components/hubs/ResearchHub';
import OptionsHub from './components/hubs/OptionsHub';
import WorkspaceHub from './components/hubs/WorkspaceHub';
import WarRoomHub from './components/hubs/WarRoomHub';
import useV2Nav from './hooks/useV2Nav';

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').then((reg) => {
      reg.update().catch(() => {});
    }).catch(() => {});
  });
}

function AppContent() {
  // One-shot referral capture for `?ref=share-*` landings
  useReferralCapture();
  const {
    paymentInfo, setPaymentInfo,
    showAuth, setShowAuth, authTab, setAuthTab,
    showSubscription, setShowSubscription,
    showAdmin, setShowAdmin,
    showWorkspace, setShowWorkspace,
    showPortfolio, setShowPortfolio,
    showSignals, setShowSignals,
    showJournal, setShowJournal,
    showStrategy, setShowStrategy,
    showMarketplace, setShowMarketplace,
    showMemory, setShowMemory,
    showPaperTrading, setShowPaperTrading,
    showSmartOrders, setShowSmartOrders,
    showRiskCalc, setShowRiskCalc,
    showScanner, setShowScanner,
    showBots, setShowBots,
    showHelp, setShowHelp,
    showDeveloper, setShowDeveloper,
    showCredits, setShowCredits,
    showFailureLoop, setShowFailureLoop,
    showAbout, setShowAbout,
    showSearchWarRoom, setShowSearchWarRoom,
    showLegal, setShowLegal,
    legalTab, setLegalTab,
    resetToken, setResetToken,
    openLogin, openRegister, openChat,
  } = useModals();
  const { user } = useAuth();
  const [showWaitlist, setShowWaitlist] = React.useState(false);
  const [tourActive, setTourActive] = React.useState(false);

  // View state: 'dashboard' | 'warroom' | 'research' | 'options' | 'workspace'
  const [activeView, setActiveView] = React.useState('dashboard');
  const [researchTab, setResearchTab] = React.useState(null);
  const [optionsTab, setOptionsTab] = React.useState(null);
  const [workspaceTab, setWorkspaceTab] = React.useState(null);
  const [warRoomTab, setWarRoomTab] = React.useState(null);
  const [showDemo, setShowDemo] = React.useState(false);
  const { enabled: v2Nav } = useV2Nav();

  const navigateTo = React.useCallback((view, subTab) => {
    setActiveView(view);
    if (view === 'research') setResearchTab(subTab || null);
    else if (view === 'options') setOptionsTab(subTab || null);
    else if (view === 'workspace') setWorkspaceTab(subTab || null);
    else if (view === 'warroom') setWarRoomTab(subTab || null);
    if (typeof window !== 'undefined') {
      window.__risedualActiveView = view;
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  React.useEffect(() => {
    if (user && !localStorage.getItem(TOUR_KEY)) {
      const timer = setTimeout(() => setTourActive(true), 2000);
      return () => clearTimeout(timer);
    }
  }, [user]);

  // Global deep-link nav bus — lets surfaces like the AI Chat Level-2 action
  // buttons route the user to a specific hub/subtab without prop-drilling.
  // Payload: { view: 'research'|'warroom'|'options'|'workspace'|'dashboard', subTab?: string }
  React.useEffect(() => {
    const handler = (e) => {
      const { view, subTab } = e?.detail || {};
      if (!view) return;
      navigateTo(view, subTab);
    };
    window.addEventListener('risedualai-navigate', handler);
    return () => window.removeEventListener('risedualai-navigate', handler);
  }, [navigateTo]);

  // OAuth Demo page — accessible without login at ?demo=oauth
  if (window.location.search.includes('demo=oauth')) {
    return (
      <React.Suspense fallback={<div className="min-h-screen bg-[#060E1F] flex items-center justify-center text-slate-400">Loading demo...</div>}>
        <AlpacaOAuthDemo />
      </React.Suspense>
    );
  }

  if (!user) {
    const openLegalTab = (tab) => { setLegalTab(tab); setShowLegal(true); };
    return (
      <div>
        <LandingPage
          onGetStarted={() => setShowWaitlist(true)}
          onLogin={() => { setAuthTab('login'); setShowAuth(true); }}
          onOpenLegal={openLegalTab}
          onTryDemo={() => setShowDemo(true)}
        />
        <Toaster />
        {showDemo && (
          <React.Suspense fallback={null}>
            <LiveDemoOverlay
              onClose={() => setShowDemo(false)}
              onJoinWaitlist={() => { setShowDemo(false); setShowWaitlist(true); }}
            />
          </React.Suspense>
        )}
        {showWaitlist && <WaitlistModal onClose={() => setShowWaitlist(false)} onOpenBetaKey={() => { setShowWaitlist(false); setAuthTab('beta'); setShowAuth(true); }} />}
        {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialTab={authTab} onOpenLegal={(tab) => { setLegalTab(tab); setShowLegal(true); }} />}
        {showLegal && <LegalPages onClose={() => setShowLegal(false)} initialTab={legalTab} />}
        {resetToken && <ResetPasswordModal token={resetToken} onClose={() => setResetToken(null)} onLoginClick={() => { setResetToken(null); setAuthTab('login'); setShowAuth(true); }} />}
      </div>
    );
  }

  const sub = () => setShowSubscription(true);

  return (
    <div className="min-h-screen bg-[#060E1F] pb-16 lg:pb-0">
      <PromoBanner onSubscribe={sub} />
      <div id="stock-ticker"><StockTicker /></div>
      <Navbar
        activeView={activeView}
        onNavigate={navigateTo}
        v2Nav={v2Nav}
        onLogin={openLogin}
        onRegister={openRegister}
        onSubscribe={sub}
        onOpenAdmin={() => setShowAdmin(true)}
        onOpenSignals={() => setShowSignals(true)}
        onOpenStrategy={() => setShowStrategy(true)}
        onOpenMarketplace={() => setShowMarketplace(true)}
        onOpenMemory={() => setShowMemory(true)}
        onOpenHelp={() => setShowHelp(true)}
        onOpenDeveloper={() => setShowDeveloper(true)}
        onOpenCredits={() => setShowCredits(true)}
        onOpenSearchWarRoom={() => setShowSearchWarRoom(true)}
        onStartTour={() => { localStorage.removeItem(TOUR_KEY); setTourActive(true); }}
        onOpenAbout={() => setShowAbout(true)}
        onOpenPortfolio={() => setShowPortfolio(true)}
        onOpenJournal={() => setShowJournal(true)}
        onOpenPaperTrading={() => setShowPaperTrading(true)}
        onOpenSmartOrders={() => setShowSmartOrders(true)}
        onOpenRiskCalc={() => setShowRiskCalc(true)}
        onOpenScanner={() => setShowScanner(true)}
        onOpenBots={() => setShowBots(true)}
        onOpenFailureLoop={() => setShowFailureLoop(true)}
      />
      <CryptoTicker />
      <AlertsPanel onSubscribe={sub} />

      <main className="max-w-[1600px] mx-auto px-3 sm:px-6 py-4 sm:py-8">

        {/* ═══ DASHBOARD ═══ */}
        {activeView === 'dashboard' && (
          <>
            {/* Compact Watchlist */}
            <div className="mb-6 sm:mb-8 animate-enter" data-testid="watchlist-section">
              <Watchlist onSubscribe={sub} />
            </div>

            {/* AI War Room (classic inline block — hidden when v2 consolidated War Room is on) */}
            {!v2Nav && (
              <div id="ai-war-room" className="mb-6 sm:mb-8 animate-enter">
                <AIWarRoom onSubscribe={sub} onLogin={openLogin} />
              </div>
            )}
            {v2Nav && (
              <div className="mb-6 sm:mb-8 animate-enter">
                <button
                  onClick={() => navigateTo('warroom')}
                  className="w-full group rounded-2xl border border-orange-500/30 bg-gradient-to-br from-orange-950/40 to-slate-900/40 p-4 sm:p-5 hover:border-orange-400/60 hover:from-orange-950/60 transition-all text-left"
                  data-testid="dashboard-warroom-card"
                >
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-orange-500/15 border border-orange-500/30 flex items-center justify-center shrink-0">
                      <span className="text-orange-400 text-lg font-bold">⚔</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <h3 className="text-white font-bold text-sm sm:text-base">AI War Room</h3>
                        <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-300">Open</span>
                      </div>
                      <p className="text-slate-400 text-xs">Adversarial AI · Predictions · Hypothesis · Signals · Intelligence — all in one command center.</p>
                    </div>
                    <span className="text-orange-400 text-xl group-hover:translate-x-1 transition-transform">&rarr;</span>
                  </div>
                </button>
              </div>
            )}

            {/* Sector Heatmap + Fear/Greed */}
            <div id="sector-heatmap" className="mb-6 sm:mb-8 animate-enter">
              <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
                <div className="lg:col-span-3"><SectorHeatmap /></div>
                <div className="lg:col-span-1"><FearGreedGauge /></div>
              </div>
            </div>

            {/* Key Signals */}
            <div id="live-insights" className="mb-6 sm:mb-8 animate-enter"><LiveInsightsFeed /></div>
            <div id="order-flow" className="mb-6 sm:mb-8 animate-enter"><OrderFlowPanel /></div>
            <div id="whale-radar" className="mb-6 sm:mb-8 animate-enter"><WhaleRadar /></div>

            {/* AI Intelligence — hidden when v2 (now lives inside War Room) */}
            {!v2Nav && (
              <div id="ai-intelligence" className="mb-6 sm:mb-8 animate-enter relative z-10">
                <AIIntelligence onSubscribe={sub} />
              </div>
            )}

            {/* Crypto summary */}
            <div id="crypto" className="mb-6 sm:mb-8 animate-enter"><CryptoSection /></div>

            {/* Explore other hubs */}
            <div className="rounded-2xl border border-slate-700/50 bg-slate-800/20 p-4 mb-6 animate-enter" data-testid="explore-hubs">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-white">Explore</h3>
                <span className="text-[10px] text-slate-500">Jump to a destination</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <button onClick={() => navigateTo('research')} className="group flex items-center gap-3 p-3 rounded-xl bg-slate-800/40 border border-slate-700/40 hover:border-[#3DE8D9]/30 hover:bg-slate-800/70 transition-all text-left" data-testid="nav-to-research">
                  <div className="w-9 h-9 rounded-lg bg-[#3DE8D9]/10 flex items-center justify-center shrink-0">
                    <span className="text-[#3DE8D9] text-sm font-bold">R</span>
                  </div>
                  <div className="min-w-0">
                    <div className="text-white text-xs font-semibold">Research</div>
                    <p className="text-slate-500 text-[10px] truncate">AI Hypothesis, Predictions, Macro</p>
                  </div>
                </button>
                <button onClick={() => navigateTo('options')} className="group flex items-center gap-3 p-3 rounded-xl bg-slate-800/40 border border-slate-700/40 hover:border-violet-400/30 hover:bg-slate-800/70 transition-all text-left" data-testid="nav-to-options">
                  <div className="w-9 h-9 rounded-lg bg-violet-500/10 flex items-center justify-center shrink-0">
                    <span className="text-violet-400 text-sm font-bold">O</span>
                  </div>
                  <div className="min-w-0">
                    <div className="text-white text-xs font-semibold">Options</div>
                    <p className="text-slate-500 text-[10px] truncate">Radar, Flow, Dark Pool</p>
                  </div>
                </button>
                <button onClick={() => navigateTo('workspace')} className="group flex items-center gap-3 p-3 rounded-xl bg-slate-800/40 border border-slate-700/40 hover:border-amber-400/30 hover:bg-slate-800/70 transition-all text-left" data-testid="nav-to-workspace">
                  <div className="w-9 h-9 rounded-lg bg-amber-500/10 flex items-center justify-center shrink-0">
                    <span className="text-amber-400 text-sm font-bold">W</span>
                  </div>
                  <div className="min-w-0">
                    <div className="text-white text-xs font-semibold">Workspace</div>
                    <p className="text-slate-500 text-[10px] truncate">Portfolio, Journal, Bots, Tools</p>
                  </div>
                </button>
              </div>
            </div>

            <AdditionalSections />
          </>
        )}

        {/* ═══ WAR ROOM (v2) ═══ */}
        {activeView === 'warroom' && (
          <WarRoomHub onSubscribe={sub} onLogin={openLogin} initialTab={warRoomTab} />
        )}

        {/* ═══ RESEARCH ═══ */}
        {activeView === 'research' && (
          <ResearchHub onSubscribe={sub} onLogin={openLogin} initialTab={researchTab} />
        )}

        {/* ═══ OPTIONS ═══ */}
        {activeView === 'options' && (
          <OptionsHub onSubscribe={sub} initialTab={optionsTab} />
        )}

        {/* ═══ WORKSPACE ═══ */}
        {activeView === 'workspace' && (
          <WorkspaceHub onSubscribe={sub} initialTab={workspaceTab} />
        )}
      </main>

      <Footer onOpenLegal={(tab) => { setLegalTab(tab); setShowLegal(true); }} />

      <RiseDualGPTChat onSubscribe={sub} />
      <MobileBottomNav
        onOpenChat={openChat}
        activeView={activeView}
        onNavigate={navigateTo}
        v2Nav={v2Nav}
      />
      <ScrollToTop />
      <Toaster />
      <OnboardingTour active={tourActive} onComplete={() => setTourActive(false)} />

      <ModalManager user={user} onNavigate={navigateTo} activeView={activeView} modals={{
        paymentInfo, setPaymentInfo, showAuth, setShowAuth, authTab, setAuthTab,
        showSubscription, setShowSubscription, showAdmin, setShowAdmin,
        showWorkspace, setShowWorkspace, showPortfolio, setShowPortfolio,
        showSignals, setShowSignals, showJournal, setShowJournal,
        showStrategy, setShowStrategy, showMarketplace, setShowMarketplace,
        showMemory, setShowMemory, showPaperTrading, setShowPaperTrading,
        showSmartOrders, setShowSmartOrders,
        showRiskCalc, setShowRiskCalc,
        showScanner, setShowScanner,
        showBots, setShowBots,
        showHelp, setShowHelp,
        showDeveloper, setShowDeveloper,
        showCredits, setShowCredits,
        showFailureLoop, setShowFailureLoop,
        showAbout, setShowAbout, showSearchWarRoom, setShowSearchWarRoom, showLegal, setShowLegal,
        legalTab, setLegalTab, resetToken, setResetToken,
      }} />
    </div>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </ErrorBoundary>
  );
}

export default App;
