import React from 'react';
import './App.css';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import useReferralCapture from './hooks/useReferralCapture';
import useSystemAccess from './hooks/useSystemAccess';
import ErrorBoundary from './components/ErrorBoundary';
import PreAuthRouter from './components/PreAuthRouter';
import AuthenticatedShell from './components/AuthenticatedShell';
import MaintenancePage from './components/MaintenancePage';
import AuthModal from './components/AuthModal';
import { Toaster } from './components/ui/sonner';
import { STORAGE_KEY as TOUR_KEY } from './components/OnboardingTour';
import useModals from './hooks/useModals';
import useV2Nav from './hooks/useV2Nav';

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').then((reg) => {
      reg.update().catch(() => {});
    }).catch(() => {});
  });
}

/**
 * AppContent — top-level orchestrator.
 *
 * Owns the modal bag, the active view, and the cross-component
 * navigation bus. Delegates rendering to PreAuthRouter (visitors)
 * or AuthenticatedShell (signed-in users). Keeping this file thin
 * means modal state and view routing live in one obvious place,
 * and the two render trees can be tested in isolation.
 */
function AppContent() {
  // One-shot referral capture for `?ref=share-*` landings
  useReferralCapture();

  const modalsHook = useModals();
  const { user } = useAuth();
  const { enabled: v2Nav } = useV2Nav();

  const [showWaitlist, setShowWaitlist] = React.useState(false);
  const [showDemo, setShowDemo] = React.useState(false);
  const [tourActive, setTourActive] = React.useState(false);

  // View state: 'dashboard' | 'warroom' | 'research' | 'options' | 'workspace' | 'terminal'
  const [activeView, setActiveView] = React.useState('dashboard');
  const [researchTab, setResearchTab] = React.useState(null);
  const [optionsTab, setOptionsTab] = React.useState(null);
  const [workspaceTab, setWorkspaceTab] = React.useState(null);
  const [warRoomTab, setWarRoomTab] = React.useState(null);

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

  // First-login onboarding tour: 2s grace then auto-open if not seen.
  React.useEffect(() => {
    if (user && !localStorage.getItem(TOUR_KEY)) {
      const timer = setTimeout(() => setTourActive(true), 2000);
      return () => clearTimeout(timer);
    }
  }, [user]);

  // Global deep-link nav bus — surfaces like the AI Chat L2 action
  // buttons can route the user to a hub/subtab without prop-drilling.
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
  // Preserves any `?ref=…` attribution so `useReferralCapture` /
  // AuthModal can still read it for signup crediting.
  React.useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const t = (params.get('warroom') || '').toUpperCase().trim();
      if (!t || !/^[A-Z0-9.]{1,8}$/.test(t)) return;
      params.delete('warroom');
      const qs = params.toString();
      const clean = window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash;
      window.history.replaceState({}, '', clean);
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: 'warroom', subTab: 'adversarial' } }));
        setTimeout(() => {
          window.dispatchEvent(new CustomEvent('risedualai-warroom', { detail: t }));
        }, 200);
      }, 50);
    } catch { /* silent */ }
  }, []);

  // Bag of modal state + handlers passed through to the render layer.
  // Includes the locally-owned waitlist/demo flags so PreAuthRouter
  // doesn't need its own copies.
  const modals = {
    ...modalsHook,
    showWaitlist, setShowWaitlist,
    showDemo, setShowDemo,
  };

  // ── Public-access lockout ─────────────────────────────────────
  // While the ML stack is in its organic data-collection window,
  // public visitors see a "Technical Difficulties" page. Owner /
  // admin emails (per backend ADMIN_EMAILS allowlist) and any
  // signed-in user (so admins can sign in via the lockout page)
  // bypass the gate. Frontend fails OPEN — a polling failure
  // never locks the user out of an otherwise-healthy site.
  const sysAccess = useSystemAccess();
  const [maintenanceLoginOpen, setMaintenanceLoginOpen] = React.useState(false);

  // Re-poll the lockout state once auth state changes — admin
  // login should make the lockout disappear without a page reload.
  React.useEffect(() => {
    if (user) {
      sysAccess.refresh();
      setMaintenanceLoginOpen(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  if (
    sysAccess.ready &&
    !sysAccess.publicAccess &&
    !sysAccess.isAdmin &&
    !user
  ) {
    return (
      <>
        <MaintenancePage
          message={sysAccess.message}
          onAdminLogin={() => setMaintenanceLoginOpen(true)}
        />
        {maintenanceLoginOpen && (
          <AuthModal
            onClose={() => {
              setMaintenanceLoginOpen(false);
              // After modal closes (whether via successful login
              // or cancel), re-poll access. If the user logged in
              // as admin, the AuthContext's user becomes truthy
              // and the useEffect above will refresh again — but
              // a redundant refresh here is harmless and faster.
              sysAccess.refresh();
            }}
          />
        )}
        <Toaster position="top-right" />
      </>
    );
  }

  // Pre-auth surfaces (OAuth demo, compliance, landing) intercept
  // before the authed shell renders. PreAuthRouter returns null
  // for signed-in users so we can fall through cleanly.
  const preAuth = (
    <PreAuthRouter user={user} modals={modals} />
  );
  if (!user || (typeof window !== 'undefined' && (
    window.location.search.includes('demo=oauth') ||
    /^\/compliance\/(?:[a-z0-9-]+-)?oauth\/?$/.test(window.location.pathname)
  ))) {
    return preAuth;
  }

  return (
    <AuthenticatedShell
      user={user}
      activeView={activeView}
      navigateTo={navigateTo}
      v2Nav={v2Nav}
      tourActive={tourActive}
      onTourComplete={() => setTourActive(false)}
      onStartTour={() => { localStorage.removeItem(TOUR_KEY); setTourActive(true); }}
      warRoomTab={warRoomTab}
      researchTab={researchTab}
      optionsTab={optionsTab}
      workspaceTab={workspaceTab}
      modals={modals}
    />
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
