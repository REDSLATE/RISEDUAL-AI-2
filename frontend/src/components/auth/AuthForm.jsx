import React, { useState } from 'react';
import { Mail, Lock, User, Eye, EyeOff, Gift } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';

const AuthForm = ({ tab, onSubmit, error, loading, refCode }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [showPw, setShowPw] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit({ email, password, name });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="auth-form">
      {tab === 'register' && refCode && (
        <div className="bg-emerald-900/20 border border-emerald-700/40 rounded-xl px-3 py-2 flex items-center gap-2" data-testid="referral-banner">
          <Gift className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          <p className="text-emerald-300 text-xs">You've been referred! Sign up to get a <strong>7-day free Pro trial</strong>.</p>
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
        <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg" data-testid="auth-error">
          {error}
        </div>
      )}

      <Button type="submit" disabled={loading}
        className="w-full bg-[#35D6C8] hover:bg-[#67E3D3] text-white font-semibold py-5 rounded-xl"
        data-testid="auth-submit-btn">
        {loading ? 'Please wait...' : tab === 'login' ? 'Log In' : 'Create Account'}
      </Button>
    </form>
  );
};

export default AuthForm;
