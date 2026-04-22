import React from 'react';
import './App.css';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import useReferralCapture from './hooks/useReferralCapture';
import { Toaster } from './components/ui/sonner';
import ErrorBoundary from './components/ErrorBoundary';
import ScrollToTop from './components/ScrollToTop';
import Footer from './components/Footer';
import Navbar from './components/Navbar';
import StockTicker from './components/StockTicker';
import MarketsSection from './components/MarketsSection';
import Watchlist from './components/Watchlist';
import AlertsPanel from './components/AlertsPanel';
import RiseDualGPTChat from './components/RiseDualGPTChat';
import MobileBottomNav from './components/MobileBottomNav';
import AIWarRoom from './components/AIWarRoom';
import AIIntelligence from './components/AIIntelligence';
import SectorHeatmap from './components/SectorHeatmap';
import FearGreedGauge from './components/FearGreedGauge';
import LiveInsightsFeed from './components/LiveInsightsFeed';
import OrderFlowPanel from './components/OrderFlowPanel';
import WhaleRadar from './components/WhaleRadar';
import AdditionalSections from './components/AdditionalSections';
import DashboardView from './components/DashboardView';
import PromoBanner from './components/PromoBanner';
import AuthModal from './components/AuthModal';
import WaitlistModal from './components/WaitlistModal';
import ResetPasswordModal from './components/ResetPasswordModal';
import LandingPage from './components/LandingPage';
import LegalPages from './components/LegalPages';
import ModalManager from './components/ModalManager';
import OnboardingTour, { STORAGE_KEY as TOUR_KEY } from './components/OnboardingTour';
import useModals from './hooks/useModals';

const AlpacaOAuthDemo = React.lazy(() => import('./components/AlpacaOAuthDemo'));
const LiveDemoOverlay = React.lazy(() => import('./components/LiveDemoOverlay'));
const ComplianceOAuth = React.lazy(() => import('./components/ComplianceOAuth'));

// Hub pages
import ResearchHub from './components/hubs/ResearchHub';
import OptionsHub from './components/hubs/OptionsHub';
import WorkspaceHub from './components/hubs/WorkspaceHub';
import WarRoomHub from './components/hubs/WarRoomHub';
const TerminalModeHub = React.lazy(() => import('./components/hubs/TerminalModeHub'));
import useV2Nav from './hooks/useV2Nav';

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').then((reg) => {
      reg.update().catch(() => {});
    }).catch(() => {});
  });
}

function AppContent() {
  // One-shot referral capture for `?ref=share-*` landings
  useReferralCapture();
  const {
    paymentInfo, setPaymentInfo,
    showAuth, setShowAuth, authTab, setAuthTab,
    initialBetaKey, setInitialBetaKey,
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
    openLogin, openRegister, openChat,
  } = useModals();
  const { user } = useAuth();
  const [showWaitlist, setShowWaitlist] = React.useState(false);
  const [tourActive, setTourActive] = React.useState(false);

  // View state: 'dashboard' | 'warroom' | 'research' | 'options' | 'workspace'
  const [activeView, setActiveView] = React.useState('dashboard');
  const [researchTab, setResearchTab] = React.useState(null);
  const [optionsTab, setOptionsTab] = React.useState(null);
  const [workspaceTab, setWorkspaceTab] = React.useState(null);
  const [warRoomTab, setWarRoomTab] = React.useState(null);
  const [showDemo, setShowDemo] = React.useState(false);
  const { enabled: v2Nav } = useV2Nav();

  const navigateTo = React.useCallback((view, subTab) => {
    setActiveView(view);
    if (view === 'research') setResearchTab(subTab || null);
    else if (view === 'options') setOptionsTab(subTab || null);
    else if (view === 'workspace') setWorkspaceTab(subTab || null);
    else if (view === 'warroom') setWarRoomTab(subTab || null);
    if (typeof window !== 'undefined') {
      window.__risedualActiveView = view;
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  React.useEffect(() => {
    if (typeof window !== 'undefined') window.__risedualActiveView = activeView;
  }, [activeView]);

  React.useEffect(() => {
    if (user && !localStorage.getItem(TOUR_KEY)) {
      const timer = setTimeout(() => setTourActive(true), 2000);
      return () => clearTimeout(timer);
    }
  }, [user]);

  // Global deep-link nav bus — lets surfaces like the AI Chat Level-2 action
  // buttons route the user to a specific hub/subtab without prop-drilling.
  // Payload: { view: 'research'|'warroom'|'options'|'workspace'|'dashboard', subTab?: string }
  React.useEffect(() => {
    const handler = (e) => {
      const { view, subTab } = e?.detail || {};
      if (!view) return;
      navigateTo(view, subTab);
    };
    window.addEventListener('risedualai-navigate', handler);
    return () => window.removeEventListener('risedualai-navigate', handler);
  }, [navigateTo]);

  // Social-share landing: `/?warroom=TICKER` → auto-open the AI War Room.
  // Matches the pattern used by `/api/share/{ticker}` which redirects here.
  // Preserves any `?ref=…` attribution so `useReferralCapture` / AuthModal
  // can still read it for signup crediting.
  React.useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const t = (params.get('warroom') || '').toUpperCase().trim();
      if (!t || !/^[A-Z0-9.]{1,8}$/.test(t)) return;
      // Strip only the warroom param; keep everything else (notably `ref`).
      params.delete('warroom');
      const qs = params.toString();
      const clean = window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash;
      window.history.replaceState({}, '', clean);
      // Defer to next tick so downstream listeners are mounted.
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: 'warroom', subTab: 'adversarial' } }));
        setTimeout(() => {
          window.dispatchEvent(new CustomEvent('risedualai-warroom', { detail: t }));
        }, 200);
      }, 50);
    } catch { /* silent */ }
  }, []);

  // OAuth Demo page — accessible without login at ?demo=oauth
  if (window.location.search.includes('demo=oauth')) {
    return (
      <React.Suspense fallback={<div className="min-h-screen bg-[#060E1F] flex items-center justify-center text-slate-400">Loading demo...</div>}>
        <AlpacaOAuthDemo />
      </React.Suspense>
    );
  }

  // Public OAuth compliance statements — linkable by broker review teams
  // (Schwab, IBKR, Alpaca, etc.) as proof of 3-legged OAuth support.
  // Pattern: /compliance/{brokerId}-oauth OR /compliance/oauth (generic).
  // No auth required, so reviewers can verify without an account.
  if (/^\/compliance\/(?:[a-z0-9-]+-)?oauth\/?$/.test(window.location.pathname)) {
    return (
      <React.Suspense fallback={<div className="min-h-screen bg-[#060E1F] flex items-center justify-center text-slate-400">Loading...</div>}>
        <ComplianceOAuth />
      </React.Suspense>
    );
  }

  if (!user) {
    const openLegalTab = (tab) => { setLegalTab(tab); setShowLegal(true); };
    return (
      <div>
        <LandingPage
          onGetStarted={() => setShowWaitlist(true)}
          onLogin={() => { setAuthTab('login'); setShowAuth(true); }}
          onOpenLegal={openLegalTab}
          onTryDemo={() => setShowDemo(true)}
          onOpenBetaRedeem={(key) => {
            setInitialBetaKey(key || '');
            setAuthTab('beta');
            setShowAuth(true);
          }}
        />
        <Toaster />
        {showDemo && (
          <React.Suspense fallback={null}>
            <LiveDemoOverlay
              onClose={() => setShowDemo(false)}
              onJoinWaitlist={() => { setShowDemo(false); setShowWaitlist(true); }}
            />
          </React.Suspense>
        )}
        {showWaitlist && <WaitlistModal onClose={() => setShowWaitlist(false)} onOpenBetaKey={() => { setShowWaitlist(false); setAuthTab('beta'); setShowAuth(true); }} />}
        {showAuth && <AuthModal onClose={() => { setShowAuth(false); setInitialBetaKey(''); }} initialTab={authTab} initialBetaKey={initialBetaKey} onOpenLegal={(tab) => { setLegalTab(tab); setShowLegal(true); }} />}
        {showLegal && <LegalPages onClose={() => setShowLegal(false)} initialTab={legalTab} />}
        {resetToken && <ResetPasswordModal token={resetToken} onClose={() => setResetToken(null)} onLoginClick={() => { setResetToken(null); setAuthTab('login'); setShowAuth(true); }} />}
      </div>
    );
  }

  const sub = () => setShowSubscription(true);

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
        onStartTour={() => { localStorage.removeItem(TOUR_KEY); setTourActive(true); }}
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
      <AlertsPanel onSubscribe={sub} />

      <main className="max-w-[1600px] mx-auto px-3 sm:px-6 py-4 sm:py-8">

        {/* ═══ DASHBOARD ═══ */}
        {activeView === 'dashboard' && (
          <DashboardView
            onSubscribe={sub}
            onLogin={openLogin}
            navigateTo={navigateTo}
            v2Nav={v2Nav}
          />
        )}

        {/* ═══ WAR ROOM (v2) ═══ */}
        {activeView === 'warroom' && (
          <WarRoomHub onSubscribe={sub} onLogin={openLogin} initialTab={warRoomTab} />
        )}

        {/* ═══ RESEARCH ═══ */}
        {activeView === 'research' && (
          <ResearchHub onSubscribe={sub} onLogin={openLogin} initialTab={researchTab} />
        )}

        {/* ═══ OPTIONS ═══ */}
        {activeView === 'options' && (
          <OptionsHub onSubscribe={sub} initialTab={optionsTab} />
        )}

        {/* ═══ WORKSPACE ═══ */}
        {activeView === 'workspace' && (
          <WorkspaceHub onSubscribe={sub} initialTab={workspaceTab} />
        )}

        {/* ═══ TERMINAL MODE ═══ */}
        {activeView === 'terminal' && (
          <React.Suspense fallback={<div className="text-slate-400 text-sm py-8 text-center font-mono">Loading terminal…</div>}>
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
      <OnboardingTour active={tourActive} onComplete={() => setTourActive(false)} />

      <ModalManager user={user} onNavigate={navigateTo} activeView={activeView} modals={{
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
        showDeveloper, setShowDeveloper,
        showCredits, setShowCredits,
        showFailureLoop, setShowFailureLoop,
        showAbout, setShowAbout, showSearchWarRoom, setShowSearchWarRoom, showLegal, setShowLegal,
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
