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

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function App() {
  const [paymentInfo, setPaymentInfo] = useState(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const paymentStatus = params.get('payment_status');
    const sessionId = params.get('session_id');

    if (paymentStatus && sessionId) {
      setPaymentInfo({ status: paymentStatus, sessionId });
      // Clean URL without reload
      window.history.replaceState({}, '', window.location.pathname);
    } else if (paymentStatus === 'cancelled') {
      setPaymentInfo({ status: 'cancelled', sessionId: null });
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  return (
    <div className="min-h-screen bg-[#0F172A]">
      {/* Stock Ticker */}
      <StockTicker />
      
      {/* Navigation */}
      <Navbar />

      {/* Crypto Ticker */}
      <CryptoTicker />

      {/* Alerts Panel */}
      <AlertsPanel />

      {/* Main Content */}
      <main className="max-w-[1600px] mx-auto px-6 py-8">
        {/* Watchlist */}
        <div className="mb-8">
          <Watchlist />
        </div>

        {/* AI Market Prediction */}
        <div id="market-prediction" className="mb-8">
          <MarketPrediction />
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

      {/* Payment Status Modal */}
      {paymentInfo && (
        <PaymentStatus
          sessionId={paymentInfo.sessionId}
          initialStatus={paymentInfo.status}
          onClose={() => setPaymentInfo(null)}
        />
      )}

      {/* Footer Note */}
      <div className="text-center py-8 text-gray-500 text-sm">
        <p>RISEDUALAI - Advanced AI-Powered Trading Platform</p>
        <p className="mt-1">Last updated on {new Date().toLocaleString('en-US', { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: 'numeric', timeZone: 'UTC' })} UTC</p>
      </div>
    </div>
  );
}

export default App;