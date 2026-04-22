/**
 * BetaSignupModal — focused "First 50" beta cohort signup.
 *
 * Distinct from the general waitlist: hard-capped, celebrates scarcity
 * via a live seat counter, and marks joiners with `cohort=first_50`
 * server-side so the admin list can track this wave separately.
 *
 * Backend: POST /api/beta/signup, GET /api/beta/stats
 */
import React, { useEffect, useState } from 'react';
import { Sparkles, X } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';
import { toast } from 'sonner';

const API = `${getApiBase()}/api/beta`;

const BetaSignupModal = ({ onClose }) => {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [reason, setReason] = useState('');
  const [honeypot, setHoneypot] = useState(''); // hidden bot trap
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [joined, setJoined] = useState(null); // { seat_number, already_joined }

  useEffect(() => {
    fetch(`${API}/stats`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setStats)
      .catch(() => {});
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (loading) return;
    if (!email || !email.includes('@')) {
      toast.error('Please enter a valid email');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API}/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(),
          name: name.trim(),
          reason: reason.trim(),
          first_name_field: honeypot, // honeypot
        }),
      });
      if (res.status === 409) {
        toast.error('Beta cohort is full — all 50 seats claimed!');
        // Refresh stats so the UI reflects the closure.
        fetch(`${API}/stats`).then((r) => r.ok && r.json().then(setStats));
        return;
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail?.[0]?.msg || err.detail || 'Signup failed');
        return;
      }
      const data = await res.json();
      setJoined(data);
    } catch {
      toast.error('Network error. Try again.');
    } finally {
      setLoading(false);
    }
  };

  const seats_remaining = stats?.seats_remaining ?? null;
  const is_full = stats?.is_full === true;

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      data-testid="beta-signup-modal"
      onClick={onClose}
    >
      <div
        className="bg-slate-900 border border-amber-500/30 rounded-2xl max-w-md w-full p-6 relative"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-white"
          data-testid="beta-close-btn"
          aria-label="Close"
        >
          <X className="w-5 h-5" />
        </button>

        {!joined ? (
          <>
            <div className="flex items-center gap-2 mb-1">
              <Sparkles className="w-5 h-5 text-amber-400" />
              <h2 className="text-xl font-bold text-white">Join the First 50</h2>
            </div>
            <p className="text-sm text-slate-400 mb-4">
              Beta access is open. Your feedback shapes what ships next.
            </p>

            {seats_remaining !== null && (
              <div
                className="mb-4 flex items-baseline justify-between bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2"
                data-testid="beta-seat-counter"
              >
                <span className="text-xs uppercase tracking-wide text-amber-300">
                  Seats remaining
                </span>
                <span className="text-lg font-bold text-amber-200 tabular-nums">
                  {seats_remaining} / {stats?.cap ?? 50}
                </span>
              </div>
            )}

            {is_full ? (
              <div className="text-center py-6">
                <p className="text-amber-300 font-semibold">
                  All 50 beta seats are claimed.
                </p>
                <p className="text-xs text-slate-400 mt-2">
                  The general waitlist is still open — reach out via the Waitlist button.
                </p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-3" data-testid="beta-form">
                {/* Honeypot — visually hidden, bots auto-fill it. */}
                <input
                  type="text"
                  tabIndex={-1}
                  autoComplete="off"
                  value={honeypot}
                  onChange={(e) => setHoneypot(e.target.value)}
                  aria-hidden="true"
                  style={{ position: 'absolute', left: '-9999px', width: 1, height: 1 }}
                  data-testid="beta-honeypot"
                />

                <input
                  type="email"
                  placeholder="your@email.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:border-amber-500/60 focus:outline-none"
                  data-testid="beta-email-input"
                />
                <input
                  type="text"
                  placeholder="Your name (optional)"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={120}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:border-amber-500/60 focus:outline-none"
                  data-testid="beta-name-input"
                />
                <textarea
                  placeholder="What excites you about beta testing? (optional)"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={500}
                  rows={3}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:border-amber-500/60 focus:outline-none resize-none"
                  data-testid="beta-reason-input"
                />
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full bg-gradient-to-r from-amber-500 to-yellow-500 hover:from-amber-400 hover:to-yellow-400 text-slate-950 font-semibold rounded-lg py-2.5 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  data-testid="beta-submit-btn"
                >
                  {loading ? 'Claiming seat…' : 'Claim My Beta Seat'}
                </button>
                <p className="text-[10px] text-slate-500 text-center">
                  We'll email when your access is ready. No spam.
                </p>
              </form>
            )}
          </>
        ) : (
          <div className="text-center py-6" data-testid="beta-success">
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-amber-500/20 border border-amber-500/40 mb-4">
              <Sparkles className="w-6 h-6 text-amber-400" />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">
              {joined.already_joined ? 'You\'re already in!' : 'Welcome to the beta.'}
            </h3>
            <p className="text-sm text-slate-400 mb-4">
              You're seat{' '}
              <span
                className="text-amber-300 font-bold tabular-nums"
                data-testid="beta-seat-number"
              >
                #{joined.seat_number}
              </span>{' '}
              of {joined.cap}.
            </p>
            <p className="text-xs text-slate-500 mb-6">
              We'll email access instructions shortly. Keep an eye on your inbox.
            </p>
            <button
              onClick={onClose}
              className="bg-slate-800 hover:bg-slate-700 text-white px-6 py-2 rounded-lg text-sm transition-colors"
              data-testid="beta-success-close-btn"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default BetaSignupModal;
