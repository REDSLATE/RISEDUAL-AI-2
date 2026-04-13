import React from 'react';
import PaymentStatus from './PaymentStatus';
import AuthModal from './AuthModal';
import SubscriptionPricing from './SubscriptionPricing';
import AdminPanel from './AdminPanel';
import UserWorkspace from './UserWorkspace';
import PortfolioAnalyzer from './PortfolioAnalyzer';
import MarketSignals from './MarketSignals';
import TradingJournal from './TradingJournal';
import StrategyBuilder from './StrategyBuilder';
import StrategyMarketplace from './StrategyMarketplace';
import MemoryDashboard from './MemoryDashboard';
import PaperTrading from './PaperTrading';
import SmartOrderPanel from './SmartOrderPanel';
import RiskCalculator from './RiskCalculator';
import MarketScanner from './MarketScanner';
import TradingBotPanel from './TradingBotPanel';
import HelpCenter from './HelpCenter';
import DeveloperPortal from './DeveloperPortal';
import AboutUs from './AboutUs';
import LegalPages from './LegalPages';
import ResetPasswordModal from './ResetPasswordModal';

const ModalManager = ({ user, modals }) => {
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
    showAbout, setShowAbout,
    showLegal, setShowLegal,
    legalTab, setLegalTab,
    resetToken, setResetToken,
  } = modals;

  return (
    <>
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
      {showSmartOrders && user && <SmartOrderPanel onClose={() => setShowSmartOrders(false)} />}
      {showRiskCalc && user && <RiskCalculator onClose={() => setShowRiskCalc(false)} onApplyToSmartOrder={(data) => { setShowRiskCalc(false); setShowSmartOrders(true); }} />}
      {showScanner && user && <MarketScanner onClose={() => setShowScanner(false)} />}
      {showBots && user && <TradingBotPanel onClose={() => setShowBots(false)} />}
      {showHelp && <HelpCenter onClose={() => setShowHelp(false)} />}
      {showDeveloper && user && <DeveloperPortal onClose={() => setShowDeveloper(false)} />}
      {showAbout && <AboutUs onClose={() => setShowAbout(false)} />}
      {showLegal && <LegalPages onClose={() => setShowLegal(false)} initialTab={legalTab} />}
      {resetToken && <ResetPasswordModal token={resetToken} onClose={() => setResetToken(null)} onLoginClick={() => { setResetToken(null); setAuthTab('login'); setShowAuth(true); }} />}
    </>
  );
};

export default ModalManager;
