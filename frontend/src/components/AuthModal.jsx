import React, { useState } from 'react';
import { X, Mail, Lock, User, Eye, EyeOff } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth } from '../contexts/AuthContext';

const AuthModal = ({ onClose, initialTab = 'login' }) => {
  const [tab, setTab] = useState(initialTab);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login, register } = useAuth();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      if (tab === 'login') {
        await login(email, password);
      } else {
        await register(email, password, name);
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
