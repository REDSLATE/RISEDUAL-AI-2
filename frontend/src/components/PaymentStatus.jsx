import React, { useState, useEffect } from 'react';
import { CheckCircle, XCircle, Loader2 } from 'lucide-react';
import { Button } from './ui/button';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const PaymentStatus = ({ sessionId, initialStatus, onClose }) => {
  const [status, setStatus] = useState(initialStatus === 'cancelled' ? 'cancelled' : 'checking');
  const [attempts, setAttempts] = useState(0);
  const maxAttempts = 6;

  useEffect(() => {
    if (!sessionId || initialStatus === 'cancelled') return;

    const pollStatus = async () => {
      try {
        const res = await fetch(`${API}/subscription/status/${sessionId}`);
        if (!res.ok) throw new Error('Failed to check status');
        const data = await res.json();

        if (data.payment_status === 'paid') {
          setStatus('success');
          return;
        }
        if (data.status === 'expired') {
          setStatus('expired');
          return;
        }

        setAttempts((prev) => {
          if (prev + 1 >= maxAttempts) {
            setStatus('timeout');
            return prev + 1;
          }
          return prev + 1;
        });
      } catch {
        setAttempts((prev) => {
          if (prev + 1 >= maxAttempts) {
            setStatus('error');
            return prev + 1;
          }
          return prev + 1;
        });
      }
    };

    if (status === 'checking' && attempts < maxAttempts) {
      const timer = setTimeout(pollStatus, 2000);
      return () => clearTimeout(timer);
    }
  }, [sessionId, initialStatus, status, attempts]);

  const renderContent = () => {
    switch (status) {
      case 'checking':
        return (
          <div className="text-center" data-testid="payment-checking">
            <Loader2 className="w-16 h-16 text-blue-500 animate-spin mx-auto mb-4" />
            <h3 className="text-xl font-bold text-white mb-2">Verifying Payment...</h3>
            <p className="text-slate-400">Please wait while we confirm your subscription.</p>
          </div>
        );
      case 'success':
        return (
          <div className="text-center" data-testid="payment-success">
            <CheckCircle className="w-16 h-16 text-lime-400 mx-auto mb-4" />
            <h3 className="text-xl font-bold text-white mb-2">Payment Successful!</h3>
            <p className="text-slate-400 mb-2">Welcome to RISEDUAL AI Premium.</p>
            <p className="text-lime-400 text-sm">Your annual subscription is now active.</p>
          </div>
        );
      case 'cancelled':
        return (
          <div className="text-center" data-testid="payment-cancelled">
            <XCircle className="w-16 h-16 text-yellow-500 mx-auto mb-4" />
            <h3 className="text-xl font-bold text-white mb-2">Payment Cancelled</h3>
            <p className="text-slate-400">No charges were made. You can try again anytime.</p>
          </div>
        );
      case 'expired':
      case 'timeout':
      case 'error':
        return (
          <div className="text-center" data-testid="payment-error">
            <XCircle className="w-16 h-16 text-orange-400 mx-auto mb-4" />
            <h3 className="text-xl font-bold text-white mb-2">
              {status === 'expired' ? 'Session Expired' : 'Status Check Failed'}
            </h3>
            <p className="text-slate-400">Please check your email for confirmation or try again.</p>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-400/25 rounded-2xl max-w-md w-full p-8" data-testid="payment-status-modal">
        {renderContent()}
        <div className="mt-6 text-center">
          <Button
            onClick={onClose}
            className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white px-8"
            data-testid="payment-status-close-btn"
          >
            {status === 'checking' ? 'Close' : 'Continue to Dashboard'}
          </Button>
        </div>
      </div>
    </div>
  );
};

export default PaymentStatus;
