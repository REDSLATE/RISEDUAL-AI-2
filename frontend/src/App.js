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
import MarketPrediction from './components/MarketPrediction';
import OptionsRadar from './components/OptionsRadar';
import OptionsFlowScreener from './components/OptionsFlowScreener';
import AdditionalSections from './components/AdditionalSections';
import DarkPoolData from './components/DarkPoolData';
import CryptoSection from './components/CryptoSection';
import RiseDualGPTChat from './components/RiseDualGPTChat';
import CompanyResearch from './components/CompanyResearch';
import MacroDashboard from './components/MacroDashboard';
import MobileBottomNav from './components/MobileBottomNav';
import AIHypothesis from './components/AIHypothesis';
import PromoBanner from './components/PromoBanner';
import AuthModal from './components/AuthModal';
import WaitlistModal from './components/WaitlistModal';
import ResetPasswordModal from './components/ResetPasswordModal';
import AIWarRoom from './components/AIWarRoom';
import AIIntelligence from './components/AIIntelligence';
import WatchlistIntelligence from './components/WatchlistIntelligence';
import ReferralLeaderboard from './components/ReferralLeaderboard';
import SectorHeatmap from './components/SectorHeatmap';
import PnLTracker from './components/PnLTracker';
import FearGreedGauge from './components/FearGreedGauge';
import LiveInsightsFeed from './components/LiveInsightsFeed';
import OrderFlowPanel from './components/OrderFlowPanel';
import WhaleRadar from './components/WhaleRadar';
import BotsDashboard from './components/BotsDashboard';
import SuccessFeeWidget from './components/SuccessFeeWidget';
import LandingPage from './components/LandingPage';
import LegalPages from './components/LegalPages';
import ModalManager from './components/ModalManager';
import OnboardingTour, { STORAGE_KEY as TOUR_KEY } from './components/OnboardingTour';
import useModals from './hooks/useModals';

// Register service worker & force-update stale ones
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').then((reg) => {
      // Force check for updates immediately
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
    showAbout, setShowAbout,
    showLegal, setShowLegal,
    legalTab, setLegalTab,
    resetToken, setResetToken,
    openLogin, openRegister, openChat,
  } = useModals();
  const { user } = useAuth();
  const [showWaitlist, setShowWaitlist] = React.useState(false);
  const [tourActive, setTourActive] = React.useState(false);

  // Auto-trigger tour on first login
  React.useEffect(() => {
    if (user && !localStorage.getItem(TOUR_KEY)) {
      const timer = setTimeout(() => setTourActive(true), 2000);
      return () => clearTimeout(timer);
    }
  }, [user]);

  // Show landing page for unauthenticated users
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

  return (
    <div className="min-h-screen bg-[#060E1F] pb-16 lg:pb-0">
      <PromoBanner onSubscribe={() => setShowSubscription(true)} />
      <div id="stock-ticker"><StockTicker /></div>
      <Navbar onLogin={openLogin} onRegister={openRegister} onSubscribe={() => setShowSubscription(true)} onOpenAdmin={() => setShowAdmin(true)} onOpenWorkspace={() => setShowWorkspace(true)} onOpenPortfolio={() => setShowPortfolio(true)} onOpenSignals={() => setShowSignals(true)} onOpenJournal={() => setShowJournal(true)} onOpenStrategy={() => setShowStrategy(true)} onOpenMarketplace={() => setShowMarketplace(true)} onOpenMemory={() => setShowMemory(true)} onOpenPaperTrading={() => setShowPaperTrading(true)} onOpenSmartOrders={() => setShowSmartOrders(true)} onOpenRiskCalc={() => setShowRiskCalc(true)} onOpenScanner={() => setShowScanner(true)} onOpenBots={() => setShowBots(true)} onOpenHelp={() => setShowHelp(true)} onStartTour={() => { localStorage.removeItem(TOUR_KEY); setTourActive(true); }} onOpenAbout={() => setShowAbout(true)} />
      <CryptoTicker />
      <AlertsPanel onSubscribe={() => setShowSubscription(true)} />

      <main className="max-w-[1600px] mx-auto px-3 sm:px-6 py-4 sm:py-8">
        <div className="mb-6 sm:mb-8 animate-enter" data-testid="watchlist-section"><Watchlist onSubscribe={() => setShowSubscription(true)} /></div>

        <div className="mb-6 sm:mb-8 animate-enter animate-enter-d1">
          <WatchlistIntelligence onSubscribe={() => setShowSubscription(true)} />
        </div>

        <div className="mb-6 sm:mb-8 animate-enter animate-enter-d2"><ReferralLeaderboard /></div>

        <div id="ai-war-room" className="mb-6 sm:mb-8 animate-enter">
          <AIWarRoom onSubscribe={() => setShowSubscription(true)} onLogin={openLogin} />
        </div>

        <div id="ai-hypothesis" className="mb-6 sm:mb-8 animate-enter relative z-20">
          <AIHypothesis onSubscribe={() => setShowSubscription(true)} onLogin={openLogin} />
        </div>

        <div id="ai-intelligence" className="mb-6 sm:mb-8 animate-enter relative z-10">
          <AIIntelligence onSubscribe={() => setShowSubscription(true)} />
        </div>

        <div id="sector-heatmap" className="mb-6 sm:mb-8 animate-enter">
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            <div className="lg:col-span-3">
              <SectorHeatmap />
            </div>
            <div className="lg:col-span-1">
              <FearGreedGauge />
            </div>
          </div>
        </div>

        <div id="pnl-tracker" className="mb-6 sm:mb-8 animate-enter">
          <PnLTracker />
        </div>

        {user && (
          <div id="success-fee" className="mb-6 sm:mb-8 animate-enter">
            <SuccessFeeWidget />
          </div>
        )}

        <div id="live-insights" className="mb-6 sm:mb-8 animate-enter">
          <LiveInsightsFeed />
        </div>
        <div id="order-flow" className="mb-6 sm:mb-8 animate-enter">
          <OrderFlowPanel />
        </div>
        <div id="whale-radar" className="mb-6 sm:mb-8 animate-enter">
          <WhaleRadar />
        </div>
        {user && (
          <div id="trading-bots" className="mb-6 sm:mb-8 animate-enter">
            <BotsDashboard onOpenBots={() => setShowBots(true)} />
          </div>
        )}


        <div id="market-prediction" className="mb-6 sm:mb-8 animate-enter"><MarketPrediction /></div>
        <div id="company-research" className="mb-6 sm:mb-8 animate-enter"><CompanyResearch /></div>
        <div id="macro-dashboard" className="mb-6 sm:mb-8 animate-enter"><MacroDashboard onSubscribe={() => setShowSubscription(true)} /></div>
        <div id="options-radar" className="animate-enter"><OptionsRadar /></div>
        <div id="options-flow" className="animate-enter"><OptionsFlowScreener /></div>
        <AdditionalSections />
        <div id="dark-pool" className="animate-enter"><DarkPoolData onSubscribe={() => setShowSubscription(true)} /></div>
        <div id="crypto" className="animate-enter"><CryptoSection /></div>
      </main>

      <Footer onOpenLegal={(tab) => { setLegalTab(tab); setShowLegal(true); }} />

      <RiseDualGPTChat onSubscribe={() => setShowSubscription(true)} />
      <MobileBottomNav onOpenChat={openChat} />
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
        showAbout, setShowAbout, showLegal, setShowLegal,
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
