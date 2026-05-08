import React from 'react';
import { Toaster } from './ui/sonner';
import Footer from './Footer';
import Navbar from './Navbar';
import StockTicker from './StockTicker';
import RiseDualGPTChat from './RiseDualGPTChat';
import MobileBottomNav from './MobileBottomNav';
import DashboardView from './DashboardView';
import PromoBanner from './PromoBanner';
import ScrollToTop from './ScrollToTop';
import ModalManager from './ModalManager';
import OnboardingTour from './OnboardingTour';

import ResearchHub from './hubs/ResearchHub';
import OptionsHub from './hubs/OptionsHub';
import WorkspaceHub from './hubs/WorkspaceHub';
import WarRoomHub from './hubs/WarRoomHub';

const TerminalModeHub = React.lazy(() => import('./hubs/TerminalModeHub'));

/**
 * Renders the authenticated user shell:
 *   - Top navigation, ticker, alerts, promo banner
 *   - Active hub (dashboard / war room / research / options / workspace / terminal)
 *   - Footer + chat dock + mobile nav
 *   - Modal layer (ModalManager)
 *
 * Receives the modals bag, view state, and navigation callbacks
 * from AppContent. Holds no state of its own — keeps the
 * authenticated layout pure and trivially testable.
 */
const AuthenticatedShell = ({
  user,
  activeView,
  navigateTo,
  v2Nav,
  tourActive,
  onTourComplete,
  onStartTour,
  warRoomTab,
  researchTab,
  optionsTab,
  workspaceTab,
  modals,
}) => {
  const {
    setShowSubscription, setShowAdmin, setShowSignals, setShowStrategy,
    setShowMarketplace, setShowMemory, setShowHelp, setShowDeveloper,
    setShowCredits, setShowSearchWarRoom, setShowAbout, setShowPortfolio,
    setShowJournal, setShowPaperTrading, setShowSmartOrders, setShowRiskCalc,
    setShowScanner, setShowBots, setShowFailureLoop,
    setShowLegal, setLegalTab,
    openLogin, openRegister, openChat,
  } = modals;

  const sub = () => setShowSubscription(true);

  // Deep-link bridge: the "View Autopsy" button on the toxic-spike
  // notification dispatches `risedual:open-admin-autopsy`. We open
  // the admin modal here; AdminPanel itself reads the sessionStorage
  // hint on mount to jump straight to the correct tab.
  React.useEffect(() => {
    const open = () => setShowAdmin(true);
    window.addEventListener('risedual:open-admin-autopsy', open);
    return () => window.removeEventListener('risedual:open-admin-autopsy', open);
  }, [setShowAdmin]);

  return (
    <div className="min-h-screen bg-[#060E1F] pb-16 lg:pb-0">
      <PromoBanner onSubscribe={sub} />
      <div id="stock-ticker"><StockTicker /></div>
      <Navbar
        activeView={activeView}
        onNavigate={navigateTo}
        v2Nav={v2Nav}
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
        onStartTour={onStartTour}
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

      <main className="max-w-[1600px] mx-auto px-3 sm:px-6 py-4 sm:py-8">
        {activeView === 'dashboard' && (
          <DashboardView
            onSubscribe={sub}
            onLogin={openLogin}
            navigateTo={navigateTo}
            v2Nav={v2Nav}
          />
        )}

        {activeView === 'warroom' && (
          <WarRoomHub onSubscribe={sub} onLogin={openLogin} initialTab={warRoomTab} />
        )}

        {activeView === 'research' && (
          <ResearchHub onSubscribe={sub} onLogin={openLogin} initialTab={researchTab} />
        )}

        {activeView === 'options' && (
          <OptionsHub onSubscribe={sub} initialTab={optionsTab} />
        )}

        {activeView === 'workspace' && (
          <WorkspaceHub onSubscribe={sub} initialTab={workspaceTab} />
        )}

        {activeView === 'terminal' && (
          <React.Suspense
            fallback={
              <div className="text-slate-400 text-sm py-8 text-center font-mono">
                Loading terminal…
              </div>
            }
          >
            <TerminalModeHub onSubscribe={sub} />
          </React.Suspense>
        )}
      </main>

      <Footer onOpenLegal={(tab) => { setLegalTab(tab); setShowLegal(true); }} />

      <RiseDualGPTChat onSubscribe={sub} />
      <MobileBottomNav
        onOpenChat={openChat}
        activeView={activeView}
        onNavigate={navigateTo}
        v2Nav={v2Nav}
      />
      <ScrollToTop />
      <Toaster />
      <OnboardingTour active={tourActive} onComplete={onTourComplete} />

      <ModalManager
        user={user}
        onNavigate={navigateTo}
        activeView={activeView}
        modals={modals}
      />
    </div>
  );
};

export default AuthenticatedShell;
