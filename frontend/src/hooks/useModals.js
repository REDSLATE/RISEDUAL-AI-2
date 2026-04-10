import { useState, useEffect } from 'react';

export default function useModals() {
  const [paymentInfo, setPaymentInfo] = useState(null);
  const [showAuth, setShowAuth] = useState(false);
  const [authTab, setAuthTab] = useState('login');
  const [showSubscription, setShowSubscription] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [showPortfolio, setShowPortfolio] = useState(false);
  const [showSignals, setShowSignals] = useState(false);
  const [showJournal, setShowJournal] = useState(false);
  const [showStrategy, setShowStrategy] = useState(false);
  const [showMarketplace, setShowMarketplace] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [resetToken, setResetToken] = useState(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const paymentStatus = params.get('payment_status');
    const sessionId = params.get('session_id');
    const rToken = params.get('reset_token');
    if (rToken) {
      setResetToken(rToken);
      window.history.replaceState({}, '', window.location.pathname);
    } else if (paymentStatus && sessionId) {
      setPaymentInfo({ status: paymentStatus, sessionId });
      window.history.replaceState({}, '', window.location.pathname);
    } else if (paymentStatus === 'cancelled') {
      setPaymentInfo({ status: 'cancelled', sessionId: null });
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  const openLogin = () => { setAuthTab('login'); setShowAuth(true); };
  const openRegister = () => { setAuthTab('register'); setShowAuth(true); };
  const openChat = () => window.dispatchEvent(new CustomEvent('risedualai-open-chat'));

  return {
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
    resetToken, setResetToken,
    openLogin, openRegister, openChat,
  };
}
