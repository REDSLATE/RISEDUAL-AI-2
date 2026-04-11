import React, { useState } from 'react';
import { Mail, ArrowLeft, CheckCircle } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

const ForgotPasswordForm = ({ onBack, initialEmail = '' }) => {
  const [email, setEmail] = useState(initialEmail);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API}/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, origin_url: window.location.origin }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Something went wrong');
      }
      setSent(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (sent) {
    return (
      <div className="text-center" data-testid="forgot-success">
        <div className="w-14 h-14 rounded-full bg-lime-900/30 border border-emerald-700/40 flex items-center justify-center mx-auto mb-4">
          <CheckCircle className="w-7 h-7 text-lime-400" />
        </div>
        <h2 className="text-white text-xl font-bold mb-2">Check your email</h2>
        <p className="text-slate-300 text-sm mb-2">
          If an account exists for <span className="text-white font-medium">{email}</span>, we've sent a password reset link.
        </p>
        <p className="text-slate-300 text-xs mb-6">The link expires in 1 hour.</p>
        <Button onClick={onBack}
          className="w-full bg-slate-800 hover:bg-slate-700 text-white font-semibold py-5 rounded-xl border border-slate-600"
          data-testid="forgot-back-to-login-btn">
          Back to Login
        </Button>
      </div>
    );
  }

  return (
    <div data-testid="forgot-form">
      <button onClick={onBack}
        className="flex items-center gap-1.5 text-slate-400 hover:text-white text-sm mb-5 transition-colors"
        data-testid="forgot-back-btn">
        <ArrowLeft className="w-4 h-4" /> Back to login
      </button>
      <h2 className="text-white text-xl font-bold mb-1">Reset your password</h2>
      <p className="text-slate-300 text-sm mb-6">Enter your email and we'll send you a link to reset your password.</p>

      {error && (
        <div className="bg-orange-900/30 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg mb-4" data-testid="auth-error">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="relative">
          <Mail className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <Input type="email" placeholder="Email address" value={email} onChange={e => setEmail(e.target.value)} required
            className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl" data-testid="forgot-email-input" />
        </div>
        <Button type="submit" disabled={loading}
          className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white font-semibold py-5 rounded-xl"
          data-testid="forgot-submit-btn">
          {loading ? 'Sending...' : 'Send Reset Link'}
        </Button>
      </form>
    </div>
  );
};

export default ForgotPasswordForm;
