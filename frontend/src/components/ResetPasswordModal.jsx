import React, { useState } from 'react';
import { X, Lock, Eye, EyeOff, CheckCircle, AlertCircle } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const ResetPasswordModal = ({ token, onClose, onLoginClick }) => {
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    if (password.length < 6) {
      setError('Password must be at least 6 characters');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API}/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: password }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to reset password');
      }
      setSuccess(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="reset-password-modal">
        <div className="bg-slate-900 rounded-2xl border border-slate-400/25 w-full max-w-md p-6 relative text-center">
          <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-white" data-testid="reset-close-btn">
            <X className="w-5 h-5" />
          </button>
          <div className="w-14 h-14 rounded-full bg-lime-900/30 border border-emerald-700/40 flex items-center justify-center mx-auto mb-4">
            <CheckCircle className="w-7 h-7 text-lime-400" />
          </div>
          <h2 className="text-white text-xl font-bold mb-2">Password Reset!</h2>
          <p className="text-slate-300 text-sm mb-6">Your password has been successfully updated. You can now log in with your new password.</p>
          <Button
            onClick={onLoginClick}
            className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white font-semibold py-5 rounded-xl"
            data-testid="reset-login-btn"
          >
            Log In
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="reset-password-modal">
      <div className="bg-slate-900 rounded-2xl border border-slate-400/25 w-full max-w-md p-6 relative">
        <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-white" data-testid="reset-close-btn">
          <X className="w-5 h-5" />
        </button>

        <h2 className="text-white text-xl font-bold mb-1">Set New Password</h2>
        <p className="text-slate-300 text-sm mb-6">Choose a strong password for your account.</p>

        {error && (
          <div className="bg-orange-900/30 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg mb-4 flex items-start gap-2" data-testid="reset-error">
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input
              type={showPw ? 'text' : 'password'}
              placeholder="New password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              minLength={6}
              className="pl-10 pr-10 bg-slate-800 border-slate-600 text-white rounded-xl"
              data-testid="reset-password-input"
            />
            <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
              {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input
              type={showPw ? 'text' : 'password'}
              placeholder="Confirm new password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              required
              minLength={6}
              className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
              data-testid="reset-confirm-input"
            />
          </div>
          <p className="text-slate-300 text-xs">Minimum 6 characters</p>
          <Button
            type="submit"
            disabled={loading}
            className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white font-semibold py-5 rounded-xl"
            data-testid="reset-submit-btn"
          >
            {loading ? 'Resetting...' : 'Reset Password'}
          </Button>
        </form>
      </div>
    </div>
  );
};

export default ResetPasswordModal;
