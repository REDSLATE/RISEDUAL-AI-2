import React, { useState, useCallback } from 'react';
import { X, ArrowRight, Users, Trophy, Share2, Copy, Check, Zap, Crown } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { toast } from './ui/sonner';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api/waitlist`;

const WaitlistModal = ({ onClose, onOpenBetaKey }) => {
  const [step, setStep] = useState('join'); // join | status | leaderboard
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [referralInput, setReferralInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [copied, setCopied] = useState(false);
  const [checkCode, setCheckCode] = useState('');

  // Extract referral code from URL on mount
  useState(() => {
    const params = new URLSearchParams(window.location.search);
    const ref = params.get('ref');
    if (ref) setReferralInput(ref);
  });

  const handleJoin = useCallback(async (e) => {
    e.preventDefault();
    if (!email) return;
    setLoading(true);
    try {
      const res = await fetch(`${API}/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, name, referral_code: referralInput, first_name_field: '' }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || 'Failed to join waitlist');
        return;
      }
      const data = await res.json();
      if (data.blocked) {
        toast.info(data.message || 'This account should log in directly.');
        return;
      }
      setResult(data);
      setStep('status');
      if (data.already_joined) {
        toast.info('Welcome back! Here\'s your waitlist status.');
      } else {
        toast.success('You\'re on the list!');
      }
    } catch {
      toast.error('Something went wrong. Try again.');
    } finally {
      setLoading(false);
    }
  }, [email, name, referralInput]);

  const handleCheckStatus = useCallback(async (e) => {
    e.preventDefault();
    if (!checkCode) return;
    setLoading(true);
    try {
      const res = await fetch(`${API}/status/${checkCode}`);
      if (!res.ok) {
        toast.error('Referral code not found');
        return;
      }
      const data = await res.json();
      setResult(data);
      setStep('status');
    } catch {
      toast.error('Failed to check status');
    } finally {
      setLoading(false);
    }
  }, [checkCode]);

  const copyLink = useCallback(() => {
    if (!result?.referral_code) return;
    const link = `${window.location.origin}?ref=${result.referral_code}`;
    navigator.clipboard.writeText(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [result]);

  const shareTwitter = useCallback(() => {
    if (!result?.referral_code) return;
    const link = `${window.location.origin}?ref=${result.referral_code}`;
    const text = `I just joined the RISEDUAL AI beta waitlist — adversarial AI trading with dual-model predictions. Join me and skip the line:`;
    window.open(`https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(link)}`, '_blank');
  }, [result]);

  const shareLinkedIn = useCallback(() => {
    if (!result?.referral_code) return;
    const link = `${window.location.origin}?ref=${result.referral_code}`;
    window.open(`https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(link)}`, '_blank');
  }, [result]);

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" data-testid="waitlist-modal">
      <div className="bg-[#0B1426] border border-slate-700/50 rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700/30">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-teal-500 to-cyan-500 flex items-center justify-center">
              <Zap className="w-4 h-4 text-white" />
            </div>
            <div>
              <h2 className="text-white text-sm font-bold">RISEDUAL AI Beta</h2>
              <p className="text-slate-400 text-[10px]">Join the Founding 50</p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white" data-testid="waitlist-close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5">
          {/* JOIN FORM */}
          {step === 'join' && (
            <div className="space-y-4">
              <div className="text-center mb-4">
                <h3 className="text-white text-base font-semibold mb-1">Jump the Line</h3>
                <p className="text-slate-400 text-xs leading-relaxed">
                  Be among the first 50 to test RISEDUAL AI's adversarial trading system.
                  <span className="text-[#3DE8D9] font-medium"> Refer friends to move up faster.</span>
                </p>
              </div>

              <form onSubmit={handleJoin} className="space-y-3">
                <Input
                  type="email" placeholder="your@email.com" value={email}
                  onChange={e => setEmail(e.target.value)} required
                  className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl"
                  data-testid="waitlist-email"
                />
                <Input
                  type="text" placeholder="Your name (optional)" value={name}
                  onChange={e => setName(e.target.value)}
                  className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl"
                  data-testid="waitlist-name"
                />
                {/* Honeypot — hidden from humans, traps bots */}
                <div style={{ position: 'absolute', left: '-9999px' }} aria-hidden="true">
                  <input type="text" name="first_name_field" tabIndex="-1" autoComplete="off" />
                </div>
                {referralInput && (
                  <div className="flex items-center gap-2 bg-[#3DE8D9]/5 border border-[#3DE8D9]/20 rounded-xl px-3 py-2">
                    <Users className="w-3.5 h-3.5 text-[#3DE8D9]" />
                    <span className="text-[#3DE8D9] text-xs">Referred by: {referralInput}</span>
                  </div>
                )}
                {!referralInput && (
                  <Input
                    type="text" placeholder="Have a referral code? (optional)" value={referralInput}
                    onChange={e => setReferralInput(e.target.value.toUpperCase())}
                    className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl"
                    data-testid="waitlist-referral"
                  />
                )}
                <Button type="submit" disabled={loading || !email}
                  className="w-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold rounded-xl py-3 hover:opacity-90"
                  data-testid="waitlist-submit">
                  {loading ? 'Joining...' : 'Join the Waitlist'}
                  {!loading && <ArrowRight className="w-4 h-4 ml-2" />}
                </Button>
              </form>

              <div className="text-center space-y-1">
                <button onClick={() => setStep('leaderboard')} className="text-slate-500 text-[10px] hover:text-slate-300 transition-colors block mx-auto">
                  Already joined? <span className="text-[#3DE8D9]">Check your status</span>
                </button>
                {onOpenBetaKey && (
                  <button onClick={onOpenBetaKey} className="text-slate-500 text-[10px] hover:text-slate-300 transition-colors block mx-auto" data-testid="open-beta-key-link">
                    Have a beta key? <span className="text-amber-300">Redeem it here</span>
                  </button>
                )}
              </div>
            </div>
          )}

          {/* STATUS VIEW */}
          {step === 'status' && result && (
            <div className="space-y-4">
              <div className="text-center mb-2">
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#3DE8D9]/10 border border-[#3DE8D9]/20 mb-3">
                  <Check className="w-3.5 h-3.5 text-[#3DE8D9]" />
                  <span className="text-[#3DE8D9] text-xs font-medium">
                    {result.already_joined ? 'Welcome Back' : 'You\'re In!'}
                  </span>
                </div>
                <h3 className="text-white text-base font-semibold">Your Position</h3>
              </div>

              {/* Stats Grid */}
              <div className="grid grid-cols-3 gap-2">
                <div className="bg-slate-800/60 rounded-xl p-3 text-center border border-slate-700/30">
                  <span className="text-xl font-bold text-white">#{result.rank || result.position}</span>
                  <p className="text-slate-400 text-[10px] mt-0.5">Your Rank</p>
                </div>
                <div className="bg-slate-800/60 rounded-xl p-3 text-center border border-slate-700/30">
                  <span className="text-xl font-bold text-[#3DE8D9]">{result.referral_count}</span>
                  <p className="text-slate-400 text-[10px] mt-0.5">Referrals</p>
                </div>
                <div className="bg-slate-800/60 rounded-xl p-3 text-center border border-slate-700/30">
                  <span className="text-xl font-bold text-violet-400">{result.total_waitlist}</span>
                  <p className="text-slate-400 text-[10px] mt-0.5">In Line</p>
                </div>
              </div>

              {/* Priority Score */}
              <div className="bg-[#111C30] border border-slate-600/30 rounded-xl p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-slate-400 text-xs">Priority Score</span>
                  <span className="text-white text-xs font-bold">{result.priority_score}</span>
                </div>
                <p className="text-slate-500 text-[10px]">
                  Each referral skips you 20 spots. Share your link to move up!
                </p>
              </div>

              {result.founding_member && (
                <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/20 rounded-xl p-3">
                  <Crown className="w-5 h-5 text-amber-400" />
                  <div>
                    <span className="text-amber-300 text-xs font-bold">Founding 50 Member</span>
                    <p className="text-amber-400/70 text-[10px]">You're part of the elite founding group!</p>
                  </div>
                </div>
              )}

              {/* Referral Link */}
              <div className="space-y-2">
                <span className="text-white text-xs font-semibold">Your Referral Link</span>
                <div className="flex items-center gap-2">
                  <Input
                    value={`${window.location.origin}?ref=${result.referral_code}`}
                    readOnly
                    className="bg-slate-800 border-slate-600 text-slate-300 text-xs rounded-xl flex-1"
                    data-testid="referral-link"
                  />
                  <Button size="sm" variant="outline" onClick={copyLink}
                    className="bg-slate-800 border-slate-600 text-white rounded-xl shrink-0"
                    data-testid="copy-referral">
                    {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                  </Button>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" onClick={shareTwitter}
                    className="flex-1 bg-slate-800 border-slate-600 text-white rounded-xl text-xs"
                    data-testid="share-twitter">
                    <Share2 className="w-3 h-3 mr-1.5" /> Share on X
                  </Button>
                  <Button size="sm" variant="outline" onClick={shareLinkedIn}
                    className="flex-1 bg-slate-800 border-slate-600 text-white rounded-xl text-xs"
                    data-testid="share-linkedin">
                    <Share2 className="w-3 h-3 mr-1.5" /> LinkedIn
                  </Button>
                </div>
              </div>

              <div className="flex items-center gap-2 text-center">
                <button onClick={() => setStep('join')} className="text-slate-500 text-[10px] hover:text-slate-300">
                  Back to join
                </button>
              </div>
            </div>
          )}

          {/* CHECK STATUS / LEADERBOARD */}
          {step === 'leaderboard' && (
            <div className="space-y-4">
              <h3 className="text-white text-sm font-semibold text-center">Check Your Status</h3>
              <form onSubmit={handleCheckStatus} className="flex gap-2">
                <Input
                  type="text" placeholder="Enter your referral code" value={checkCode}
                  onChange={e => setCheckCode(e.target.value.toUpperCase())}
                  className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl flex-1"
                  data-testid="check-code-input"
                />
                <Button type="submit" disabled={loading || !checkCode}
                  className="bg-[#3DE8D9] text-white rounded-xl px-4"
                  data-testid="check-status-btn">
                  Check
                </Button>
              </form>
              <div className="text-center">
                <button onClick={() => setStep('join')} className="text-[#3DE8D9] text-xs hover:underline">
                  Don't have a code? Join the waitlist
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-slate-700/30 bg-slate-900/40">
          <div className="flex items-center justify-center gap-4 text-[10px] text-slate-500">
            <span>Founding 50 get exclusive perks</span>
            <span className="w-1 h-1 rounded-full bg-slate-700" />
            <span>30-day launch window</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default WaitlistModal;
