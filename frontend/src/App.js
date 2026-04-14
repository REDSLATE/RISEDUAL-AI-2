import React from 'react';
import './App.css';
import { AuthProvider, useAuth } from './contexts/AuthContext';
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

// Hub pages
import ResearchHub from './components/hubs/ResearchHub';
import OptionsHub from './components/hubs/OptionsHub';
import WorkspaceHub from './components/hubs/WorkspaceHub';

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').then((reg) => {
      reg.update().catch(() => {});
    }).catch(() => {});
  });
}

function AppContent() {
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

  // View state: 'dashboard' | 'research' | 'options' | 'workspace'
  const [activeView, setActiveView] = React.useState('dashboard');
  const [researchTab, setResearchTab] = React.useState(null);
  const [optionsTab, setOptionsTab] = React.useState(null);
  const [workspaceTab, setWorkspaceTab] = React.useState(null);

  const navigateTo = React.useCallback((view, subTab) => {
    setActiveView(view);
    if (view === 'research') setResearchTab(subTab || null);
    else if (view === 'options') setOptionsTab(subTab || null);
    else if (view === 'workspace') setWorkspaceTab(subTab || null);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  React.useEffect(() => {
    if (user && !localStorage.getItem(TOUR_KEY)) {
      const timer = setTimeout(() => setTourActive(true), 2000);
      return () => clearTimeout(timer);
    }
  }, [user]);

  if (!user) {
    const openLegalTab = (tab) => { setLegalTab(tab); setShowLegal(true); };
    return (
      <div>
        <LandingPage onGetStarted={() => setShowWaitlist(true)} onLogin={() => { setAuthTab('login'); setShowAuth(true); }} onOpenLegal={openLegalTab} />
        <Toaster />
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

            {/* AI War Room */}
            <div id="ai-war-room" className="mb-6 sm:mb-8 animate-enter">
              <AIWarRoom onSubscribe={sub} onLogin={openLogin} />
            </div>

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

            {/* AI Intelligence */}
            <div id="ai-intelligence" className="mb-6 sm:mb-8 animate-enter relative z-10">
              <AIIntelligence onSubscribe={sub} />
            </div>

            {/* Crypto summary */}
            <div id="crypto" className="mb-6 sm:mb-8 animate-enter"><CryptoSection /></div>

            {/* Quick-nav into hubs */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6 animate-enter">
              <button onClick={() => navigateTo('research')} className="group p-5 rounded-2xl border border-slate-700/50 bg-slate-800/30 hover:bg-slate-800/60 hover:border-[#3DE8D9]/30 transition-all text-left" data-testid="nav-to-research">
                <div className="text-[#3DE8D9] text-lg font-bold mb-1">Research Hub</div>
                <p className="text-slate-400 text-xs">AI Hypothesis, Market Prediction, Company Research, Macro</p>
              </button>
              <button onClick={() => navigateTo('options')} className="group p-5 rounded-2xl border border-slate-700/50 bg-slate-800/30 hover:bg-slate-800/60 hover:border-violet-400/30 transition-all text-left" data-testid="nav-to-options">
                <div className="text-violet-400 text-lg font-bold mb-1">Options Hub</div>
                <p className="text-slate-400 text-xs">Options Radar, Flow Screener, Dark Pool Analytics</p>
              </button>
              <button onClick={() => navigateTo('workspace')} className="group p-5 rounded-2xl border border-slate-700/50 bg-slate-800/30 hover:bg-slate-800/60 hover:border-amber-400/30 transition-all text-left" data-testid="nav-to-workspace">
                <div className="text-amber-400 text-lg font-bold mb-1">Workspace</div>
                <p className="text-slate-400 text-xs">Portfolio, Journal, Bots, Paper Trading, Risk Tools</p>
              </button>
            </div>

            <AdditionalSections />
          </>
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
          <WorkspaceHub
            onSubscribe={sub}
            initialTab={workspaceTab}
            onOpenPortfolio={() => setShowPortfolio(true)}
            onOpenJournal={() => setShowJournal(true)}
            onOpenPaperTrading={() => setShowPaperTrading(true)}
            onOpenBots={() => setShowBots(true)}
            onOpenSmartOrders={() => setShowSmartOrders(true)}
            onOpenRiskCalc={() => setShowRiskCalc(true)}
            onOpenScanner={() => setShowScanner(true)}
            onOpenFailureLoop={() => setShowFailureLoop(true)}
          />
        )}
      </main>

      <Footer onOpenLegal={(tab) => { setLegalTab(tab); setShowLegal(true); }} />

      <RiseDualGPTChat onSubscribe={sub} />
      <MobileBottomNav
        onOpenChat={openChat}
        activeView={activeView}
        onNavigate={navigateTo}
      />
      <ScrollToTop />
      <Toaster />
      <OnboardingTour active={tourActive} onComplete={() => setTourActive(false)} />

      <ModalManager user={user} modals={{
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
