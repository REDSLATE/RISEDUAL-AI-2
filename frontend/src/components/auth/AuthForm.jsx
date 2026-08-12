import React, { useState } from 'react';
import { Mail, Lock, User, Eye, EyeOff, Gift } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';

const AuthForm = ({ tab, onSubmit, error, loading, refCode, onOpenLegal }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [agreed, setAgreed] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit({ email, password, name });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="auth-form">
      {tab === 'register' && refCode && (
        <div className="bg-green-600 border border-emerald-700/40 rounded-xl px-3 py-2 flex items-center gap-2" data-testid="referral-banner">
          <Gift className="w-4 h-4 text-lime-400 flex-shrink-0" />
          <p className="text-lime-300 text-xs">You've been referred! Sign up to get a <strong>7-day free Pro trial</strong>.</p>
        </div>
      )}
      {tab === 'register' && (
        <div className="relative">
          <User className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <Input placeholder="Full name" value={name} onChange={e => setName(e.target.value)}
            className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl" data-testid="auth-name-input" />
        </div>
      )}
      <div className="relative">
        <Mail className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
        <Input type="email" placeholder="Email address" value={email} onChange={e => setEmail(e.target.value)} required
          className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl" data-testid="auth-email-input" />
      </div>
      <div className="relative">
        <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
        <Input type={showPw ? 'text' : 'password'} placeholder="Password" value={password}
          onChange={e => setPassword(e.target.value)} required minLength={6}
          className="pl-10 pr-10 bg-slate-800 border-slate-600 text-white rounded-xl" data-testid="auth-password-input" />
        <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
          {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>

      {error && (
        <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg" data-testid="auth-error">
          {error}
        </div>
      )}

      {tab === 'register' && (
        <label className="flex items-start gap-2 cursor-pointer" data-testid="auth-terms-label">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-0.5 w-4 h-4 rounded border-slate-600 bg-slate-800 text-[#3DE8D9] focus:ring-[#3DE8D9]"
            data-testid="auth-terms-checkbox"
          />
          <span className="text-slate-400 text-[11px] leading-relaxed">
            I agree to the{' '}
            <button type="button" onClick={() => onOpenLegal?.('terms')} className="text-[#3DE8D9] hover:underline">Terms of Service</button>,{' '}
            <button type="button" onClick={() => onOpenLegal?.('privacy')} className="text-[#3DE8D9] hover:underline">Privacy Policy</button>, and{' '}
            <button type="button" onClick={() => onOpenLegal?.('risk')} className="text-[#3DE8D9] hover:underline">Risk Disclosure</button>.
          </span>
        </label>
      )}

      <Button type="submit" disabled={loading || (tab === 'register' && !agreed)}
        className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white font-semibold py-5 rounded-xl"
        data-testid="auth-submit-btn">
        {loading ? 'Please wait...' : tab === 'login' ? 'Log In' : 'Create Account'}
      </Button>

      <div className="relative my-2" data-testid="auth-divider">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-slate-700/60" />
        </div>
        <div className="relative flex justify-center text-[10px] uppercase tracking-wider">
          <span className="bg-slate-900 px-2 text-slate-500">or</span>
        </div>
      </div>

      <button
        type="button"
        onClick={() => {
          // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
          const redirectUrl = window.location.origin + '/';
          window.location.href =
            'https://auth.emergentagent.com/?redirect=' + encodeURIComponent(redirectUrl);
        }}
        disabled={loading || (tab === 'register' && !agreed)}
        className="w-full flex items-center justify-center gap-2 bg-white hover:bg-slate-100 disabled:opacity-40 text-slate-800 font-semibold py-3 rounded-xl border border-slate-300 transition"
        data-testid="auth-google-btn"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.4h6.5c-.3 1.5-1.1 2.7-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.7z"/>
          <path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-2.9l-3.9-3c-1.1.7-2.5 1.2-4.1 1.2-3.1 0-5.8-2.1-6.7-4.9H1.3v3.1C3.3 21.4 7.3 24 12 24z"/>
          <path fill="#FBBC05" d="M5.3 14.3c-.2-.7-.4-1.4-.4-2.3 0-.8.1-1.6.4-2.3V6.6H1.3C.5 8.2 0 10 0 12s.5 3.8 1.3 5.4l4-3.1z"/>
          <path fill="#EA4335" d="M12 4.8c1.8 0 3.4.6 4.6 1.8l3.4-3.4C18 1.2 15.2 0 12 0 7.3 0 3.3 2.6 1.3 6.6l4 3.1C6.2 6.9 8.9 4.8 12 4.8z"/>
        </svg>
        {tab === 'login' ? 'Continue with Google' : 'Sign up with Google'}
      </button>
    </form>
  );
};

export default AuthForm;
