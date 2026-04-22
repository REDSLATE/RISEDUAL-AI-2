/**
 * BetaSignupModal — focused "First 50" beta cohort signup.
 *
 * Entitlements: Pro access + 30,000 credits + 30-day trial +
 * founding_member badge.
 *
 * Flow variants the success state handles:
 *   - New email  → backend returns a beta_key; we surface it with
 *     a copy button and a CTA to open the redemption tab in the
 *     auth modal.
 *   - Existing user (already registered) → backend upgrades in
 *     place; success state just says "your account is now Pro."
 *
 * Backend:
 *   POST /api/beta/signup     — claim seat
 *   GET  /api/beta/stats      — live counter
 */
import React, { useEffect, useState } from 'react';
import { Copy, Check, Sparkles, X } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';
import { toast } from 'sonner';

const API = `${getApiBase()}/api/beta`;

const Entitlements = () => (
  <ul
    className="mt-3 space-y-1.5 text-xs text-slate-300"
    data-testid="beta-entitlements"
  >
    <li className="flex items-start gap-2">
      <Check className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
      <span><strong className="text-amber-200">Pro access</strong> — full platform unlock for 30 days</span>
    </li>
    <li className="flex items-start gap-2">
      <Check className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
      <span><strong className="text-amber-200">30,000 credits</strong> on the house (2× Pro monthly)</span>
    </li>
    <li className="flex items-start gap-2">
      <Check className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
      <span><strong className="text-amber-200">Founding member</strong> badge</span>
    </li>
  </ul>
);

const BetaKeyDisplay = ({ beta_key }) => {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(beta_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      toast.success('Beta key copied');
    } catch {
      toast.error('Copy failed — please select and copy manually');
    }
  };
  return (
    <div
      className="bg-slate-800/80 border border-amber-500/30 rounded-lg p-3 flex items-center justify-between gap-2"
      data-testid="beta-key-display"
    >
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-wide text-amber-300 mb-0.5">
          Your Beta Key
        </div>
        <div
          className="font-mono text-sm text-white truncate"
          data-testid="beta-key-value"
        >
          {beta_key}
        </div>
      </div>
      <button
        onClick={copy}
        className="shrink-0 bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 px-3 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1"
        data-testid="beta-key-copy-btn"
      >
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  );
};

const BetaSignupModal = ({ onClose, onOpenBetaRedeem }) => {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [reason, setReason] = useState('');
  const [honeypot, setHoneypot] = useState('');
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

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
          first_name_field: honeypot,
        }),
      });
      if (res.status === 409) {
        toast.error('Beta cohort is full — all 50 seats claimed!');
        fetch(`${API}/stats`).then((r) => r.ok && r.json().then(setStats));
        return;
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail?.[0]?.msg || err.detail || 'Signup failed');
        return;
      }
      setResult(await res.json());
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
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[70] flex items-center justify-center p-4"
      data-testid="beta-signup-modal"
      onClick={onClose}
    >
      <div
        className="bg-slate-900 border border-amber-500/30 rounded-2xl max-w-md w-full p-6 relative max-h-[90vh] overflow-y-auto"
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

        {!result ? (
          <>
            <div className="flex items-center gap-2 mb-1">
              <Sparkles className="w-5 h-5 text-amber-400" />
              <h2 className="text-xl font-bold text-white">Join the First 50</h2>
            </div>
            <p className="text-sm text-slate-400 mb-3">
              Beta access is open. Your feedback shapes what ships next.
            </p>

            <Entitlements />

            {seats_remaining !== null && (
              <div
                className="mt-4 mb-4 flex items-baseline justify-between bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2"
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
                  The general waitlist is still open.
                </p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-3" data-testid="beta-form">
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
                  No spam. Your feedback builds the product.
                </p>
              </form>
            )}
          </>
        ) : (
          <div className="text-center pt-2 pb-4" data-testid="beta-success">
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-amber-500/20 border border-amber-500/40 mb-4">
              <Sparkles className="w-6 h-6 text-amber-400" />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">
              {result.status === 'upgraded'
                ? result.already_granted
                  ? 'You\'re already in the beta!'
                  : 'Account upgraded to Pro.'
                : result.already_joined
                ? 'You\'re already in!'
                : 'Seat secured.'}
            </h3>
            <p className="text-sm text-slate-400 mb-4">
              Seat{' '}
              <span
                className="text-amber-300 font-bold tabular-nums"
                data-testid="beta-seat-number"
              >
                #{result.seat_number}
              </span>{' '}
              of {result.cap}.
            </p>

            {result.status === 'upgraded' ? (
              <>
                <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-left text-sm text-slate-300 mb-4">
                  {result.already_granted ? (
                    <>
                      Your account already has the <strong className="text-amber-200">Pro</strong> beta perks applied. Nothing new to do.
                    </>
                  ) : (
                    <>
                      We've upgraded your account. You now have{' '}
                      <strong className="text-amber-200">Pro access</strong> and{' '}
                      <strong className="text-amber-200">
                        {(result.grant_credits ?? 30000).toLocaleString()} credits
                      </strong>{' '}
                      in your wallet — good for {result.trial_days} days.
                    </>
                  )}
                </div>
              </>
            ) : result.beta_key ? (
              <div className="text-left mb-4">
                <p className="text-xs text-slate-400 mb-2 text-center">
                  Use this key to create your Pro account:
                </p>
                <BetaKeyDisplay beta_key={result.beta_key} />
                <p className="text-[10px] text-slate-500 mt-2 text-center">
                  Unlocks Pro + {(result.grant_credits ?? 30000).toLocaleString()} credits ·
                  expires in 14 days
                </p>
              </div>
            ) : null}

            <div className="flex flex-col gap-2">
              {result.beta_key && onOpenBetaRedeem && (
                <button
                  onClick={() => {
                    onOpenBetaRedeem(result.beta_key);
                  }}
                  className="bg-gradient-to-r from-amber-500 to-yellow-500 hover:from-amber-400 hover:to-yellow-400 text-slate-950 font-semibold px-6 py-2.5 rounded-lg text-sm transition-all"
                  data-testid="beta-redeem-now-btn"
                >
                  Redeem Now → Create Pro Account
                </button>
              )}
              <button
                onClick={onClose}
                className="bg-slate-800 hover:bg-slate-700 text-white px-6 py-2 rounded-lg text-sm transition-colors"
                data-testid="beta-success-close-btn"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default BetaSignupModal;
