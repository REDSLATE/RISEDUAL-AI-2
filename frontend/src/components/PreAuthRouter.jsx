import React from 'react';
import { Toaster } from './ui/sonner';
import LandingPage from './LandingPage';
import LegalPages from './LegalPages';
import AuthModal from './AuthModal';
import WaitlistModal from './WaitlistModal';
import ResetPasswordModal from './ResetPasswordModal';

const AlpacaOAuthDemo = React.lazy(() => import('./AlpacaOAuthDemo'));
const LiveDemoOverlay = React.lazy(() => import('./LiveDemoOverlay'));
const ComplianceOAuth = React.lazy(() => import('./ComplianceOAuth'));

const LoadingShell = ({ label }) => (
  <div className="min-h-screen bg-[#060E1F] flex items-center justify-center text-slate-400">
    {label}
  </div>
);

/**
 * Routes the unauthenticated visitor experience:
 *   1. ?demo=oauth         → Alpaca OAuth demo (lazy)
 *   2. /compliance/.../oauth → Public OAuth compliance page (lazy)
 *   3. Otherwise           → LandingPage + auth/waitlist/reset modals
 *
 * Returns null when the visitor is signed in — caller renders the
 * authenticated shell instead.
 */
const PreAuthRouter = ({ user, modals }) => {
  // OAuth demo route — accessible without login
  if (typeof window !== 'undefined' && window.location.search.includes('demo=oauth')) {
    return (
      <React.Suspense fallback={<LoadingShell label="Loading demo..." />}>
        <AlpacaOAuthDemo />
      </React.Suspense>
    );
  }

  // Public OAuth compliance statements — linkable by broker review teams
  // (Schwab, IBKR, Alpaca, etc.) as proof of 3-legged OAuth support.
  // No auth required, so reviewers can verify without an account.
  if (
    typeof window !== 'undefined' &&
    /^\/compliance\/(?:[a-z0-9-]+-)?oauth\/?$/.test(window.location.pathname)
  ) {
    return (
      <React.Suspense fallback={<LoadingShell label="Loading..." />}>
        <ComplianceOAuth />
      </React.Suspense>
    );
  }

  if (user) return null;

  const {
    showAuth, setShowAuth, authTab, setAuthTab,
    initialBetaKey, setInitialBetaKey,
    showLegal, setShowLegal, legalTab, setLegalTab,
    resetToken, setResetToken,
    showWaitlist, setShowWaitlist,
    showDemo, setShowDemo,
  } = modals;

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
      {showWaitlist && (
        <WaitlistModal
          onClose={() => setShowWaitlist(false)}
          onOpenBetaKey={() => { setShowWaitlist(false); setAuthTab('beta'); setShowAuth(true); }}
        />
      )}
      {showAuth && (
        <AuthModal
          onClose={() => { setShowAuth(false); setInitialBetaKey(''); }}
          initialTab={authTab}
          initialBetaKey={initialBetaKey}
          onOpenLegal={openLegalTab}
        />
      )}
      {showLegal && <LegalPages onClose={() => setShowLegal(false)} initialTab={legalTab} />}
      {resetToken && (
        <ResetPasswordModal
          token={resetToken}
          onClose={() => setResetToken(null)}
          onLoginClick={() => { setResetToken(null); setAuthTab('login'); setShowAuth(true); }}
        />
      )}
    </div>
  );
};

export default PreAuthRouter;
