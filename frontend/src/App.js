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
import AuthModal from './components/AuthModal';
import SubscriptionPricing from './components/SubscriptionPricing';
import AdminPanel from './components/AdminPanel';
import UserWorkspace from './components/UserWorkspace';

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
  const { user } = useAuth();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const paymentStatus = params.get('payment_status');
    const sessionId = params.get('session_id');
    if (paymentStatus && sessionId) {
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
      <div id="stock-ticker"><StockTicker /></div>
      <Navbar onLogin={openLogin} onRegister={openRegister} onSubscribe={() => setShowSubscription(true)} onOpenAdmin={() => setShowAdmin(true)} onOpenWorkspace={() => setShowWorkspace(true)} />
      <CryptoTicker />
      <AlertsPanel onSubscribe={() => setShowSubscription(true)} />

      <main className="max-w-[1600px] mx-auto px-3 sm:px-6 py-4 sm:py-8">
        <div className="mb-6 sm:mb-8"><Watchlist /></div>

        {/* AI Investment Hypothesis (Paywalled) */}
        <div id="ai-hypothesis" className="mb-6 sm:mb-8">
          <AIHypothesis onSubscribe={() => setShowSubscription(true)} onLogin={openLogin} />
        </div>

        <div id="market-prediction" className="mb-6 sm:mb-8"><MarketPrediction /></div>
        <div id="company-research" className="mb-6 sm:mb-8"><CompanyResearch /></div>
        <div id="macro-dashboard" className="mb-6 sm:mb-8"><MacroDashboard /></div>
        <div id="options-radar"><OptionsRadar /></div>
        <div id="options-flow"><OptionsFlowScreener /></div>
        <AdditionalSections />
        <div id="dark-pool"><DarkPoolData /></div>
        <div id="crypto"><CryptoSection /></div>
      </main>

      <TradeGPTChat />
      <MobileBottomNav onOpenChat={openChat} />

      {paymentInfo && <PaymentStatus sessionId={paymentInfo.sessionId} initialStatus={paymentInfo.status} onClose={() => setPaymentInfo(null)} />}
      {showAuth && <AuthModal onClose={() => setShowAuth(false)} initialTab={authTab} />}
      {showSubscription && <SubscriptionPricing onClose={() => setShowSubscription(false)} />}
      {showAdmin && user?.role === 'owner' && <AdminPanel onClose={() => setShowAdmin(false)} />}
      {showWorkspace && user && <UserWorkspace onClose={() => setShowWorkspace(false)} />}

      <div className="text-center py-6 sm:py-8 text-gray-500 text-xs sm:text-sm">
        <p>RISEDUALAI - Advanced AI-Powered Trading Platform</p>
        <p className="mt-1">Last updated on {new Date().toLocaleString('en-US', { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: 'numeric', timeZone: 'UTC' })} UTC</p>
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
