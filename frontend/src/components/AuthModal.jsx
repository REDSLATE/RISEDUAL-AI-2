import React, { useState, useEffect } from 'react';
import { X, Mail, Lock, User, Eye, EyeOff, Gift, ArrowLeft, CheckCircle } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth } from '../contexts/AuthContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AuthModal = ({ onClose, initialTab = 'login' }) => {
  const [tab, setTab] = useState(initialTab);
  const [view, setView] = useState('form'); // 'form' | 'forgot' | 'forgot-sent'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [forgotEmail, setForgotEmail] = useState('');
  const { login, register } = useAuth();

  const refCode = new URLSearchParams(window.location.search).get('ref') || '';

  useEffect(() => {
    if (refCode) setTab('register');
  }, [refCode]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
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

  const handleForgotPassword = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API}/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: forgotEmail }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Something went wrong');
      }
      setView('forgot-sent');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Forgot Password - Email Input View
  if (view === 'forgot') {
    return (
      <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="auth-modal">
        <div className="bg-slate-900 rounded-2xl border border-slate-700/50 w-full max-w-md p-6 relative">
          <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-white" data-testid="auth-close-btn">
            <X className="w-5 h-5" />
          </button>

          <button
            onClick={() => { setView('form'); setError(''); }}
            className="flex items-center gap-1.5 text-slate-400 hover:text-white text-sm mb-5 transition-colors"
            data-testid="forgot-back-btn"
          >
            <ArrowLeft className="w-4 h-4" /> Back to login
          </button>

          <h2 className="text-white text-xl font-bold mb-1">Reset your password</h2>
          <p className="text-slate-400 text-sm mb-6">
            Enter your email and we'll send you a link to reset your password.
          </p>

          {error && (
            <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg mb-4" data-testid="auth-error">
              {error}
            </div>
          )}

          <form onSubmit={handleForgotPassword} className="space-y-4">
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
              <Input
                type="email"
                placeholder="Email address"
                value={forgotEmail}
                onChange={e => setForgotEmail(e.target.value)}
                required
                className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
                data-testid="forgot-email-input"
              />
            </div>
            <Button
              type="submit"
              disabled={loading}
              className="w-full bg-[#0052FF] hover:bg-[#2563EB] text-white font-semibold py-5 rounded-xl"
              data-testid="forgot-submit-btn"
            >
              {loading ? 'Sending...' : 'Send Reset Link'}
            </Button>
          </form>
        </div>
      </div>
    );
  }

  // Forgot Password - Success View
  if (view === 'forgot-sent') {
    return (
      <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="auth-modal">
        <div className="bg-slate-900 rounded-2xl border border-slate-700/50 w-full max-w-md p-6 relative text-center">
          <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-white" data-testid="auth-close-btn">
            <X className="w-5 h-5" />
          </button>

          <div className="w-14 h-14 rounded-full bg-emerald-900/30 border border-emerald-700/40 flex items-center justify-center mx-auto mb-4">
            <CheckCircle className="w-7 h-7 text-emerald-400" />
          </div>
          <h2 className="text-white text-xl font-bold mb-2">Check your email</h2>
          <p className="text-slate-400 text-sm mb-2">
            If an account exists for <span className="text-white font-medium">{forgotEmail}</span>, we've sent a password reset link.
          </p>
          <p className="text-slate-500 text-xs mb-6">The link expires in 1 hour.</p>

          <Button
            onClick={() => { setView('form'); setError(''); setForgotEmail(''); }}
            className="w-full bg-slate-800 hover:bg-slate-700 text-white font-semibold py-5 rounded-xl border border-slate-600"
            data-testid="forgot-back-to-login-btn"
          >
            Back to Login
          </Button>
        </div>
      </div>
    );
  }

  // Default Login / Register View
  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="auth-modal">
      <div className="bg-slate-900 rounded-2xl border border-slate-700/50 w-full max-w-md p-6 relative">
        <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-white" data-testid="auth-close-btn">
          <X className="w-5 h-5" />
        </button>

        {/* Tab Toggle */}
        <div className="flex gap-1 bg-slate-800 rounded-xl p-1 mb-6">
          {['login', 'register'].map(t => (
            <button
              key={t}
              onClick={() => { setTab(t); setError(''); }}
              data-testid={`auth-tab-${t}`}
              className={`flex-1 py-2 text-sm font-medium rounded-lg transition-all ${
                tab === t ? 'bg-[#0052FF] text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              {t === 'login' ? 'Log In' : 'Sign Up'}
            </button>
          ))}
        </div>

        <h2 className="text-white text-xl font-bold mb-1">
          {tab === 'login' ? 'Welcome back' : 'Create your account'}
        </h2>
        <p className="text-slate-400 text-sm mb-6">
          {tab === 'login' ? 'Log in to access your workspace' : 'Start your AI trading journey'}
        </p>

        {error && (
          <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg mb-4" data-testid="auth-error">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {tab === 'register' && refCode && (
            <div className="bg-emerald-900/20 border border-emerald-700/40 rounded-xl px-3 py-2 flex items-center gap-2" data-testid="referral-banner">
              <Gift className="w-4 h-4 text-emerald-400 flex-shrink-0" />
              <p className="text-emerald-300 text-xs">You've been referred! Sign up to get a <strong>7-day free Pro trial</strong>.</p>
            </div>
          )}
          {tab === 'register' && (
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
              <Input
                placeholder="Full name"
                value={name}
                onChange={e => setName(e.target.value)}
                className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
                data-testid="auth-name-input"
              />
            </div>
          )}
          <div className="relative">
            <Mail className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input
              type="email"
              placeholder="Email address"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
              data-testid="auth-email-input"
            />
          </div>
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input
              type={showPw ? 'text' : 'password'}
              placeholder="Password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              minLength={6}
              className="pl-10 pr-10 bg-slate-800 border-slate-600 text-white rounded-xl"
              data-testid="auth-password-input"
            />
            <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
              {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>

          {tab === 'login' && (
            <div className="text-right -mt-1">
              <button
                type="button"
                onClick={() => { setView('forgot'); setError(''); setForgotEmail(email); }}
                className="text-[#0052FF] hover:text-[#2563EB] text-sm font-medium transition-colors"
                data-testid="forgot-password-link"
              >
                Forgot password?
              </button>
            </div>
          )}

          <Button
            type="submit"
            disabled={loading}
            className="w-full bg-[#0052FF] hover:bg-[#2563EB] text-white font-semibold py-5 rounded-xl"
            data-testid="auth-submit-btn"
          >
            {loading ? 'Please wait...' : tab === 'login' ? 'Log In' : 'Create Account'}
          </Button>
        </form>
      </div>
    </div>
  );
};

export default AuthModal;
