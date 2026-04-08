import React, { useState, useEffect } from 'react';
import './App.css';
import { AuthProvider, useAuth } from './contexts/AuthContext';
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
import TradeGPTChat from './components/TradeGPTChat';
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
import AIIntelligence from './components/AIIntelligence';
import WatchlistIntelligence from './components/WatchlistIntelligence';
import ResetPasswordModal from './components/ResetPasswordModal';

// Register service worker
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  });
}

function AppContent() {
  const [paymentInfo, setPaymentInfo] = useState(null);
  const [showAuth, setShowAuth] = useState(false);
  const [authTab, setAuthTab] = useState('login');
  const [showSubscription, setShowSubscription] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [showPortfolio, setShowPortfolio] = useState(false);
  const [showSignals, setShowSignals] = useState(false);
  const [showJournal, setShowJournal] = useState(false);
  const [showStrategy, setShowStrategy] = useState(false);
  const [showMarketplace, setShowMarketplace] = useState(false);
  const [resetToken, setResetToken] = useState(null);
  const { user } = useAuth();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const paymentStatus = params.get('payment_status');
    const sessionId = params.get('session_id');
    const rToken = params.get('reset_token');
    if (rToken) {
      setResetToken(rToken);
      window.history.replaceState({}, '', window.location.pathname);
    } else if (paymentStatus && sessionId) {
      setPaymentInfo({ status: paymentStatus, sessionId });
      window.history.replaceState({}, '', window.location.pathname);
    } else if (paymentStatus === 'cancelled') {
      setPaymentInfo({ status: 'cancelled', sessionId: null });
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  const openChat = () => window.dispatchEvent(new CustomEvent('risedualai-open-chat'));
  const openLogin = () => { setAuthTab('login'); setShowAuth(true); };
  const openRegister = () => { setAuthTab('register'); setShowAuth(true); };

  return (
    <div className="min-h-screen bg-[#0F172A] pb-16 lg:pb-0">
      <PromoBanner onSubscribe={() => setShowSubscription(true)} />
      <div id="stock-ticker"><StockTicker /></div>
      <Navbar onLogin={openLogin} onRegister={openRegister} onSubscribe={() => setShowSubscription(true)} onOpenAdmin={() => setShowAdmin(true)} onOpenWorkspace={() => setShowWorkspace(true)} onOpenPortfolio={() => setShowPortfolio(true)} onOpenSignals={() => setShowSignals(true)} onOpenJournal={() => setShowJournal(true)} onOpenStrategy={() => setShowStrategy(true)} onOpenMarketplace={() => setShowMarketplace(true)} />
      <CryptoTicker />
      <AlertsPanel onSubscribe={() => setShowSubscription(true)} />

      <main className="max-w-[1600px] mx-auto px-3 sm:px-6 py-4 sm:py-8">
        <div className="mb-6 sm:mb-8"><Watchlist onSubscribe={() => setShowSubscription(true)} /></div>
        
        {/* Watchlist Intelligence */}
        <div className="mb-6 sm:mb-8">
          <WatchlistIntelligence onSubscribe={() => setShowSubscription(true)} />
        </div>

        {/* Referral Leaderboard */}
        <div className="mb-6 sm:mb-8"><ReferralLeaderboard /></div>

        {/* AI Investment Hypothesis (Paywalled) */}
        <div id="ai-hypothesis" className="mb-6 sm:mb-8">
          <AIHypothesis onSubscribe={() => setShowSubscription(true)} onLogin={openLogin} />
        </div>

        {/* AI Intelligence Hub */}
        <div id="ai-intelligence" className="mb-6 sm:mb-8">
          <AIIntelligence onSubscribe={() => setShowSubscription(true)} />
        </div>

        <div id="market-prediction" className="mb-6 sm:mb-8"><MarketPrediction /></div>
        <div id="company-research" className="mb-6 sm:mb-8"><CompanyResearch /></div>
        <div id="macro-dashboard" className="mb-6 sm:mb-8"><MacroDashboard onSubscribe={() => setShowSubscription(true)} /></div>
        <div id="options-radar"><OptionsRadar /></div>
        <div id="options-flow"><OptionsFlowScreener /></div>
        <AdditionalSections />
        <div id="dark-pool"><DarkPoolData onSubscribe={() => setShowSubscription(true)} /></div>
        <div id="crypto"><CryptoSection /></div>
      </main>

      <TradeGPTChat onSubscribe={() => setShowSubscription(true)} />
      <MobileBottomNav onOpenChat={openChat} />

      {paymentInfo && <PaymentStatus sessionId={paymentInfo.sessionId} initialStatus={paymentInfo.status} onClose={() => setPaymentInfo(null)} />}
      {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialTab={authTab} />}
      {showSubscription && <SubscriptionPricing onClose={() => setShowSubscription(false)} />}
      {showAdmin && user?.role === 'owner' && <AdminPanel onClose={() => setShowAdmin(false)} />}
      {showWorkspace && user && <UserWorkspace onClose={() => setShowWorkspace(false)} onSubscribe={() => { setShowWorkspace(false); setShowSubscription(true); }} />}
      {showPortfolio && user && <PortfolioAnalyzer onClose={() => setShowPortfolio(false)} onSubscribe={() => { setShowPortfolio(false); setShowSubscription(true); }} />}
      {showSignals && user && <MarketSignals onClose={() => setShowSignals(false)} onSubscribe={() => { setShowSignals(false); setShowSubscription(true); }} />}
      {showJournal && user && <TradingJournal onClose={() => setShowJournal(false)} onSubscribe={() => { setShowJournal(false); setShowSubscription(true); }} />}
      {showStrategy && user && <StrategyBuilder onClose={() => setShowStrategy(false)} onSubscribe={() => { setShowStrategy(false); setShowSubscription(true); }} />}
      {showMarketplace && <StrategyMarketplace onClose={() => setShowMarketplace(false)} onSubscribe={() => { setShowMarketplace(false); setShowSubscription(true); }} />}
      {resetToken && <ResetPasswordModal token={resetToken} onClose={() => setResetToken(null)} onLoginClick={() => { setResetToken(null); setAuthTab('login'); setShowAuth(true); }} />}

      <div className="text-center py-6 sm:py-8 text-gray-500 text-xs sm:text-sm">
        <div className="flex items-center justify-center gap-2 mb-1">
          <img src="/logo-icon.png" alt="RISEDUAL AI" className="w-[23px] h-[23px] object-contain brightness-125" />
          <p>RISEDUAL AI - Advanced AI-Powered Trading Platform</p>
        </div>
        <p>Last updated on {new Date().toLocaleString('en-US', { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: 'numeric', timeZone: 'UTC' })} UTC</p>
      </div>
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

export default App;
