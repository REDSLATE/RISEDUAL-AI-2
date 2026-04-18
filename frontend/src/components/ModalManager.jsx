import React from 'react';
import ErrorBoundary from './ErrorBoundary';
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
import CreditStore from './CreditStore';
import FailureLoopDashboard from './FailureLoopDashboard';
import SearchWarRoom from './SearchWarRoom';
import AboutUs from './AboutUs';
import LegalPages from './LegalPages';
import ResetPasswordModal from './ResetPasswordModal';

const ModalManager = ({ user, modals, onNavigate, activeView }) => {
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
  } = modals;

  return (
    <>
      <ErrorBoundary>
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
        {showHelp && <HelpCenter onClose={() => setShowHelp(false)} onNavigate={onNavigate} contextHub={activeView} />}
        {showDeveloper && user && <DeveloperPortal onClose={() => setShowDeveloper(false)} />}
        {showCredits && user && <CreditStore onClose={() => setShowCredits(false)} onSubscribe={() => { setShowCredits(false); modals.setShowSubscription(true); }} />}
        {showFailureLoop && user && <FailureLoopDashboard onClose={() => setShowFailureLoop(false)} />}
        {showSearchWarRoom && user && (
          <div className="fixed inset-0 z-[90] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" onClick={() => setShowSearchWarRoom(false)}>
            <div className="bg-[#0A1628] border border-white/10 rounded-2xl w-full max-w-4xl max-h-[85vh] overflow-y-auto p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
              <SearchWarRoom onClose={() => setShowSearchWarRoom(false)} />
            </div>
          </div>
        )}
        {showAbout && <AboutUs onClose={() => setShowAbout(false)} />}
        {showLegal && <LegalPages onClose={() => setShowLegal(false)} initialTab={legalTab} />}
        {resetToken && <ResetPasswordModal token={resetToken} onClose={() => setResetToken(null)} onLoginClick={() => { setResetToken(null); setAuthTab('login'); setShowAuth(true); }} />}
      </ErrorBoundary>
    </>
  );
};

export default ModalManager;
