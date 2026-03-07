import React from 'react';
import './App.css';
import Navbar from './components/Navbar';
import StockTicker from './components/StockTicker';
import CryptoTicker from './components/CryptoTicker';
import OptionsRadar from './components/OptionsRadar';
import OptionsFlowScreener from './components/OptionsFlowScreener';
import AdditionalSections from './components/AdditionalSections';
import DarkPoolData from './components/DarkPoolData';
import CryptoSection from './components/CryptoSection';
import TradeGPTChat from './components/TradeGPTChat';

function App() {
  return (
    <div className="min-h-screen bg-[#0f0f10]">
      {/* Stock Ticker */}
      <StockTicker />
      
      {/* Navigation */}
      <Navbar />

      {/* Crypto Ticker */}
      <CryptoTicker />

      {/* Main Content */}
      <main className="max-w-[1600px] mx-auto px-6 py-8">
        {/* AI Options Radar */}
        <OptionsRadar />

        {/* Options Flow Screener */}
        <OptionsFlowScreener />

        {/* Additional Sections */}
        <AdditionalSections />

        {/* Dark Pool Data */}
        <DarkPoolData />

        {/* Cryptocurrency Section */}
        <CryptoSection />
      </main>

      {/* TradeGPT Chat */}
      <TradeGPTChat />

      {/* Footer Note */}
      <div className="text-center py-8 text-gray-500 text-sm">
        <p>RISEDUALAI - Advanced AI-Powered Trading Platform</p>
        <p className="mt-1">Last updated on {new Date().toLocaleString('en-US', { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: 'numeric', timeZone: 'UTC' })} UTC</p>
      </div>
    </div>
  );
}

export default App;