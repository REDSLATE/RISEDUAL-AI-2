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
  const [showPaperTrading, setShowPaperTrading] = useState(false);
  const [showSmartOrders, setShowSmartOrders] = useState(false);
  const [showRiskCalc, setShowRiskCalc] = useState(false);
  const [showScanner, setShowScanner] = useState(false);
  const [showBots, setShowBots] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showDeveloper, setShowDeveloper] = useState(false);
  const [showCredits, setShowCredits] = useState(false);
  const [showFailureLoop, setShowFailureLoop] = useState(false);
  const [showAbout, setShowAbout] = useState(false);
  const [showLegal, setShowLegal] = useState(false);
  const [legalTab, setLegalTab] = useState('terms');
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
    showLegal, setShowLegal,
    legalTab, setLegalTab,
    resetToken, setResetToken,
    openLogin, openRegister, openChat,
  };
}
