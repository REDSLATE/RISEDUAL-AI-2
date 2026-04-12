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
import PaymentStatus from './components/PaymentStatus';
import CompanyResearch from './components/CompanyResearch';
import MacroDashboard from './components/MacroDashboard';
import MobileBottomNav from './components/MobileBottomNav';
import AIHypothesis from './components/AIHypothesis';
import PromoBanner from './components/PromoBanner';
import AuthModal from './components/AuthModal';
import SubscriptionPricing from './components/SubscriptionPricing';
import AdminPanel from './components/AdminPanel';
import UserWorkspace from './components/UserWorkspace';
import PortfolioAnalyzer from './components/PortfolioAnalyzer';
import MarketSignals from './components/MarketSignals';
import ReferralLeaderboard from './components/ReferralLeaderboard';
import TradingJournal from './components/TradingJournal';
import StrategyBuilder from './components/StrategyBuilder';
import StrategyMarketplace from './components/StrategyMarketplace';
import AIWarRoom from './components/AIWarRoom';
import AIIntelligence from './components/AIIntelligence';
import WatchlistIntelligence from './components/WatchlistIntelligence';
import WaitlistModal from './components/WaitlistModal';
import ResetPasswordModal from './components/ResetPasswordModal';
import SectorHeatmap from './components/SectorHeatmap';
import PnLTracker from './components/PnLTracker';
import FearGreedGauge from './components/FearGreedGauge';
import LiveInsightsFeed from './components/LiveInsightsFeed';
import OrderFlowPanel from './components/OrderFlowPanel';
import WhaleRadar from './components/WhaleRadar';
import MemoryDashboard from './components/MemoryDashboard';
import LandingPage from './components/LandingPage';
import PaperTrading from './components/PaperTrading';
import AboutUs from './components/AboutUs';
import LegalPages from './components/LegalPages';
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
    showAbout, setShowAbout,
    showLegal, setShowLegal,
    legalTab, setLegalTab,
    resetToken, setResetToken,
    openLogin, openRegister, openChat,
  } = useModals();
  const { user } = useAuth();
  const [showWaitlist, setShowWaitlist] = React.useState(false);

  // Show landing page for unauthenticated users
  if (!user) {
    const openLegalTab = (tab) => { setLegalTab(tab); setShowLegal(true); };
    return (
      <div>
        <LandingPage onGetStarted={() => setShowWaitlist(true)} onOpenLegal={openLegalTab} />
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
      <Navbar onLogin={openLogin} onRegister={openRegister} onSubscribe={() => setShowSubscription(true)} onOpenAdmin={() => setShowAdmin(true)} onOpenWorkspace={() => setShowWorkspace(true)} onOpenPortfolio={() => setShowPortfolio(true)} onOpenSignals={() => setShowSignals(true)} onOpenJournal={() => setShowJournal(true)} onOpenStrategy={() => setShowStrategy(true)} onOpenMarketplace={() => setShowMarketplace(true)} onOpenMemory={() => setShowMemory(true)} onOpenPaperTrading={() => setShowPaperTrading(true)} onOpenAbout={() => setShowAbout(true)} />
      <CryptoTicker />
      <AlertsPanel onSubscribe={() => setShowSubscription(true)} />

      <main className="max-w-[1600px] mx-auto px-3 sm:px-6 py-4 sm:py-8">
        <div className="mb-6 sm:mb-8 animate-enter"><Watchlist onSubscribe={() => setShowSubscription(true)} /></div>

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

        <div id="live-insights" className="mb-6 sm:mb-8 animate-enter">
          <LiveInsightsFeed />
        </div>
        <div id="order-flow" className="mb-6 sm:mb-8 animate-enter">
          <OrderFlowPanel />
        </div>
        <div id="whale-radar" className="mb-6 sm:mb-8 animate-enter">
          <WhaleRadar />
        </div>


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

      {paymentInfo && <PaymentStatus sessionId={paymentInfo.sessionId} initialStatus={paymentInfo.status} onClose={() => setPaymentInfo(null)} />}
      {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialTab={authTab} onOpenLegal={(tab) => { setLegalTab(tab); setShowLegal(true); }} />}
      {showSubscription && <SubscriptionPricing onClose={() => setShowSubscription(false)} />}
      {showAdmin && (user?.role === 'owner' || user?.role === 'admin') && <AdminPanel onClose={() => setShowAdmin(false)} />}
      {showWorkspace && user && <UserWorkspace onClose={() => setShowWorkspace(false)} onSubscribe={() => { setShowWorkspace(false); setShowSubscription(true); }} />}
      {showPortfolio && user && <PortfolioAnalyzer onClose={() => setShowPortfolio(false)} onSubscribe={() => { setShowPortfolio(false); setShowSubscription(true); }} />}
      {showSignals && user && <MarketSignals onClose={() => setShowSignals(false)} onSubscribe={() => { setShowSignals(false); setShowSubscription(true); }} />}
      {showJournal && user && <TradingJournal onClose={() => setShowJournal(false)} onSubscribe={() => { setShowJournal(false); setShowSubscription(true); }} />}
      {showStrategy && user && <StrategyBuilder onClose={() => setShowStrategy(false)} onSubscribe={() => { setShowStrategy(false); setShowSubscription(true); }} />}
      {showMarketplace && <StrategyMarketplace onClose={() => setShowMarketplace(false)} onSubscribe={() => { setShowMarketplace(false); setShowSubscription(true); }} />}
      {showMemory && user && <MemoryDashboard onClose={() => setShowMemory(false)} onSubscribe={() => { setShowMemory(false); setShowSubscription(true); }} />}
      {showPaperTrading && user && <PaperTrading onClose={() => setShowPaperTrading(false)} />}
      {showAbout && <AboutUs onClose={() => setShowAbout(false)} />}
      {showLegal && <LegalPages onClose={() => setShowLegal(false)} initialTab={legalTab} />}
      {resetToken && <ResetPasswordModal token={resetToken} onClose={() => setResetToken(null)} onLoginClick={() => { setResetToken(null); setAuthTab('login'); setShowAuth(true); }} />}
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
