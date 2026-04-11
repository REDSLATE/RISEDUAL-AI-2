import React, { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import AuthForm from './auth/AuthForm';
import ForgotPasswordForm from './auth/ForgotPasswordForm';

const AuthModal = ({ onClose, initialTab = 'login', onOpenLegal }) => {
  const [tab, setTab] = useState(initialTab);
  const [view, setView] = useState('form');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [lastEmail, setLastEmail] = useState('');
  const { login, register } = useAuth();

  const refCode = new URLSearchParams(window.location.search).get('ref') || '';

  useEffect(() => {
    if (refCode) setTab('register');
  }, [refCode]);

  const handleSubmit = async ({ email, password, name }) => {
    setError('');
    setLoading(true);
    setLastEmail(email);
    try {
      if (tab === 'login') {
        await login(email, password);
      } else {
        await register(email, password, name, refCode);
      }
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="auth-modal">
      <div className="bg-slate-900 rounded-2xl border border-slate-400/25 w-full max-w-md p-6 relative">
        <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-white" data-testid="auth-close-btn">
          <X className="w-5 h-5" />
        </button>

        {view === 'forgot' ? (
          <ForgotPasswordForm onBack={() => { setView('form'); setError(''); }} initialEmail={lastEmail} />
        ) : (
          <>
            <div className="flex gap-1 bg-slate-800 rounded-xl p-1 mb-6">
              {['login', 'register'].map(t => (
                <button key={t} onClick={() => { setTab(t); setError(''); }} data-testid={`auth-tab-${t}`}
                  className={`flex-1 py-2 text-sm font-medium rounded-lg transition-all ${
                    tab === t ? 'bg-[#3DE8D9] text-white' : 'text-slate-400 hover:text-white'
                  }`}>
                  {t === 'login' ? 'Log In' : 'Sign Up'}
                </button>
              ))}
            </div>

            <h2 className="text-white text-xl font-bold mb-1">
              {tab === 'login' ? 'Welcome back' : 'Create your account'}
            </h2>
            <p className="text-slate-300 text-sm mb-6">
              {tab === 'login' ? 'Log in to access your workspace' : 'Start your AI trading journey'}
            </p>

            <AuthForm tab={tab} onSubmit={handleSubmit} error={error} loading={loading} refCode={refCode} onOpenLegal={onOpenLegal} />

            {tab === 'login' && (
              <div className="text-right mt-3">
                <button type="button" onClick={() => { setView('forgot'); setError(''); }}
                  className="text-[#3DE8D9] hover:text-[#7AEEE0] text-sm font-medium transition-colors"
                  data-testid="forgot-password-link">
                  Forgot password?
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default AuthModal;
