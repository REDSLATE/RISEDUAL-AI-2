import React, { useState, useEffect } from 'react';
import './App.css';
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

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Register service worker
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  });
}

function App() {
  const [paymentInfo, setPaymentInfo] = useState(null);

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

  const openChat = () => {
    window.dispatchEvent(new CustomEvent('risedualai-open-chat'));
  };

  return (
    <div className="min-h-screen bg-[#0F172A] pb-16 lg:pb-0">
      {/* Stock Ticker */}
      <div id="stock-ticker">
        <StockTicker />
      </div>
      
      {/* Navigation */}
      <Navbar />

      {/* Crypto Ticker */}
      <CryptoTicker />

      {/* Alerts Panel */}
      <AlertsPanel />

      {/* Main Content */}
      <main className="max-w-[1600px] mx-auto px-3 sm:px-6 py-4 sm:py-8">
        {/* Watchlist */}
        <div className="mb-6 sm:mb-8">
          <Watchlist />
        </div>

        {/* AI Market Prediction */}
        <div id="market-prediction" className="mb-6 sm:mb-8">
          <MarketPrediction />
        </div>

        {/* Company Research (Perplexity-style) */}
        <div id="company-research" className="mb-6 sm:mb-8">
          <CompanyResearch />
        </div>

        {/* Macro Intelligence Dashboard */}
        <div id="macro-dashboard" className="mb-6 sm:mb-8">
          <MacroDashboard />
        </div>

        {/* AI Options Radar */}
        <div id="options-radar">
          <OptionsRadar />
        </div>

        {/* Options Flow Screener */}
        <div id="options-flow">
          <OptionsFlowScreener />
        </div>

        {/* Additional Sections - contains momentum, fast-movers, unusual-volume IDs */}
        <AdditionalSections />

        {/* Dark Pool Data */}
        <div id="dark-pool">
          <DarkPoolData />
        </div>

        {/* Cryptocurrency Section */}
        <div id="crypto">
          <CryptoSection />
        </div>
      </main>

      {/* TradeGPT Chat */}
      <TradeGPTChat />

      {/* Mobile Bottom Navigation */}
      <MobileBottomNav onOpenChat={openChat} />

      {/* Payment Status Modal */}
      {paymentInfo && (
        <PaymentStatus
          sessionId={paymentInfo.sessionId}
          initialStatus={paymentInfo.status}
          onClose={() => setPaymentInfo(null)}
        />
      )}

      {/* Footer Note */}
      <div className="text-center py-6 sm:py-8 text-gray-500 text-xs sm:text-sm">
        <p>RISEDUALAI - Advanced AI-Powered Trading Platform</p>
        <p className="mt-1">Last updated on {new Date().toLocaleString('en-US', { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: 'numeric', timeZone: 'UTC' })} UTC</p>
      </div>
    </div>
  );
}

export default App;