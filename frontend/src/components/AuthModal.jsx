import React, { useState, useEffect } from 'react';
import { X, Key, ArrowRight } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import AuthForm from './auth/AuthForm';
import ForgotPasswordForm from './auth/ForgotPasswordForm';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api/auth`;

const AuthModal = ({ onClose, initialTab = 'login', initialBetaKey = '', onOpenLegal }) => {
  const [tab, setTab] = useState(initialTab);
  const [view, setView] = useState('form');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [lastEmail, setLastEmail] = useState('');
  const { login, register } = useAuth();

  // Beta key state
  const [betaKey, setBetaKey] = useState(initialBetaKey);
  const [betaEmail, setBetaEmail] = useState('');
  const [betaPassword, setBetaPassword] = useState('');
  const [betaName, setBetaName] = useState('');

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

  const handleBetaRedeem = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API}/redeem-beta-key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          beta_key: betaKey.trim().toUpperCase(),
          email: betaEmail.trim(),
          password: betaPassword,
          name: betaName.trim(),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || 'Failed to redeem beta key');
        return;
      }
      toast.success(data.founding_member
        ? 'Welcome, Founding Member! Your Pro access is active.'
        : 'Beta key redeemed! Your Pro trial is active.'
      );
      // Auto-login with the returned tokens
      await login(betaEmail.trim(), betaPassword);
      onClose();
    } catch (err) {
      setError(err.message || 'Something went wrong');
    } finally {
      setLoading(false);
    }
  };

  const tabs = ['login', 'register', 'beta'];

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
              {tabs.map(t => (
                <button key={t} onClick={() => { setTab(t); setError(''); }} data-testid={`auth-tab-${t}`}
                  className={`flex-1 py-2 text-sm font-medium rounded-lg transition-all ${
                    tab === t ? 'bg-[#3DE8D9] text-white' : 'text-slate-400 hover:text-white'
                  }`}>
                  {t === 'login' ? 'Log In' : t === 'register' ? 'Sign Up' : 'Beta Key'}
                </button>
              ))}
            </div>

            {tab === 'beta' ? (
              <>
                <div className="flex items-center gap-2 mb-4">
                  <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#3DE8D9] to-cyan-500 flex items-center justify-center">
                    <Key className="w-4 h-4 text-white" />
                  </div>
                  <div>
                    <h2 className="text-white text-lg font-bold">Redeem Beta Key</h2>
                    <p className="text-slate-400 text-xs">Enter the key from your invite email</p>
                  </div>
                </div>

                <form onSubmit={handleBetaRedeem} className="space-y-3">
                  <Input
                    type="text" placeholder="BETA-XXXX-XXXX-XXXX" value={betaKey}
                    onChange={e => setBetaKey(e.target.value.toUpperCase())}
                    className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl font-mono text-center tracking-wider"
                    required data-testid="beta-key-input"
                  />
                  <Input
                    type="email" placeholder="your@email.com" value={betaEmail}
                    onChange={e => setBetaEmail(e.target.value)}
                    className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl"
                    required data-testid="beta-email-input"
                  />
                  <Input
                    type="text" placeholder="Your name" value={betaName}
                    onChange={e => setBetaName(e.target.value)}
                    className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl"
                    data-testid="beta-name-input"
                  />
                  <Input
                    type="password" placeholder="Choose a password (min 6 chars)" value={betaPassword}
                    onChange={e => setBetaPassword(e.target.value)}
                    className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl"
                    required minLength={6} data-testid="beta-password-input"
                  />

                  {error && (
                    <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-2">
                      <p className="text-red-400 text-xs">{error}</p>
                    </div>
                  )}

                  <Button type="submit" disabled={loading || !betaKey || !betaEmail || !betaPassword}
                    className="w-full bg-gradient-to-r from-[#3DE8D9] to-cyan-500 text-white font-semibold rounded-xl py-3"
                    data-testid="beta-redeem-btn">
                    {loading ? 'Activating...' : 'Activate Beta Access'}
                    {!loading && <ArrowRight className="w-4 h-4 ml-2" />}
                  </Button>
                </form>

                <p className="text-slate-500 text-[10px] text-center mt-3">
                  Beta keys are sent via email to waitlist invitees. Keys expire after 7 days.
                </p>
              </>
            ) : (
              <>
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
          </>
        )}
      </div>
    </div>
  );
};

export default AuthModal;
