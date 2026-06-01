import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Beaker, Flame, AlertTriangle, X } from 'lucide-react';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { toast } from 'sonner';

const API = `${getApiBase()}/api`;
const HOLD_MS = 5000; // 5-second hold-to-confirm for LIVE
const POLL_MS = 1000;

/**
 * TradingModePill — always-visible navbar switch between PAPER and LIVE.
 *
 * UX contract (per product decisions on 2026-04-29):
 *   • Pill in navbar; PAPER = mint, LIVE = orange.
 *   • Click → modal.
 *   • PAPER → LIVE requires user to type "LIVE" AND hold the button for 5s.
 *   • LIVE → PAPER requires only a single click (less risky direction).
 *   • 30s cooldown after every flip; UI shows live countdown.
 *   • Default mode for new users is PAPER (server-side default).
 */
const TradingModePill = () => {
  const { user, refreshAuth } = useAuth();
  const mode = user?.trading_mode || 'paper';

  const [open, setOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [holdProgress, setHoldProgress] = useState(0); // 0–100
  const [holding, setHolding] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [serverError, setServerError] = useState('');
  const holdTimerRef = useRef(null);
  const cooldownTimerRef = useRef(null);

  const targetMode = mode === 'paper' ? 'live' : 'paper';
  const goingLive = targetMode === 'live';

  // Poll cooldown from server when modal opens or mode changes
  const fetchState = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/trading-mode/current`);
      if (res.ok) {
        const data = await res.json();
        setCooldown(data.cooldown_remaining_s || 0);
      }
    } catch {
      // Silent — non-critical
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    fetchState();
  }, [user, fetchState]);

  // Cross-component "open modal" channel — listened to by
  // TradingModeBanner (and any future surface that wants to nudge
  // the user to switch). Works regardless of whether THIS pill is
  // visually rendered (mobile layout hides the pill inside the
  // navbar's ``hidden lg:flex`` wrapper, but the listener still
  // fires and the modal renders via portal).
  useEffect(() => {
    if (!user) return;
    const onOpenRequest = () => {
      fetchState();
      setOpen(true);
    };
    window.addEventListener('risedual:open-trading-mode-modal', onOpenRequest);
    return () => window.removeEventListener(
      'risedual:open-trading-mode-modal', onOpenRequest,
    );
  }, [user, fetchState]);

  // Cooldown ticker
  useEffect(() => {
    if (cooldown <= 0) return;
    cooldownTimerRef.current = setInterval(() => {
      setCooldown((c) => Math.max(0, c - 1));
    }, POLL_MS);
    return () => clearInterval(cooldownTimerRef.current);
  }, [cooldown]);

  const resetForm = () => {
    setConfirmText('');
    setHoldProgress(0);
    setHolding(false);
    setServerError('');
    if (holdTimerRef.current) {
      clearInterval(holdTimerRef.current);
      holdTimerRef.current = null;
    }
  };

  const closeModal = () => {
    resetForm();
    setOpen(false);
  };

  const startHold = () => {
    if (goingLive && confirmText.trim().toUpperCase() !== 'LIVE') return;
    if (cooldown > 0 || submitting) return;
    setHolding(true);
    const start = Date.now();
    holdTimerRef.current = setInterval(() => {
      const elapsed = Date.now() - start;
      const pct = Math.min(100, (elapsed / HOLD_MS) * 100);
      setHoldProgress(pct);
      if (elapsed >= HOLD_MS) {
        clearInterval(holdTimerRef.current);
        holdTimerRef.current = null;
        executeSwitch();
      }
    }, 50);
  };

  const cancelHold = () => {
    if (holdTimerRef.current) {
      clearInterval(holdTimerRef.current);
      holdTimerRef.current = null;
    }
    setHolding(false);
    setHoldProgress(0);
  };

  const executeSwitch = async () => {
    setSubmitting(true);
    setServerError('');
    try {
      const body = { mode: targetMode };
      if (goingLive) body.confirm_text = confirmText.trim().toUpperCase();
      const res = await authFetch(`${API}/trading-mode/switch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = data?.detail?.message || 'Mode switch failed';
        setServerError(msg);
        toast.error(msg);
        cancelHold();
        return;
      }
      setCooldown(data.cooldown_remaining_s || 30);
      toast.success(
        targetMode === 'live'
          ? 'Switched to LIVE — real money mode active'
          : 'Switched to PAPER — practice mode'
      );
      // Refresh user object so the pill repaints with new mode
      if (refreshAuth) await refreshAuth();
      closeModal();
    } catch (e) {
      setServerError('Network error');
      toast.error('Network error — try again');
      cancelHold();
    } finally {
      setSubmitting(false);
    }
  };

  // Quick-flip path for LIVE → PAPER (no type-text, no hold; single confirm click)
  const executeQuickFlip = async () => {
    setSubmitting(true);
    setServerError('');
    try {
      const res = await authFetch(`${API}/trading-mode/switch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'paper' }),
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = data?.detail?.message || 'Mode switch failed';
        setServerError(msg);
        toast.error(msg);
        return;
      }
      setCooldown(data.cooldown_remaining_s || 30);
      toast.success('Switched to PAPER — practice mode');
      if (refreshAuth) await refreshAuth();
      closeModal();
    } catch {
      setServerError('Network error');
      toast.error('Network error — try again');
    } finally {
      setSubmitting(false);
    }
  };

  if (!user) return null;

  const isLive = mode === 'live';
  const pillBase =
    'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider border transition-all hover:scale-[1.03] cursor-pointer';
  const pillClass = isLive
    ? 'bg-orange-500/15 text-orange-300 border-orange-500/40 shadow-[0_0_12px_rgba(249,115,22,0.25)]'
    : 'bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/40';

  return (
    <>
      <button
        type="button"
        className={`${pillBase} ${pillClass}`}
        onClick={() => {
          fetchState();
          setOpen(true);
        }}
        title={`Trading mode: ${mode.toUpperCase()} — click to switch`}
        data-testid="trading-mode-pill"
        data-mode={mode}
      >
        {isLive ? <Flame className="w-3 h-3" /> : <Beaker className="w-3 h-3" />}
        <span>{mode}</span>
      </button>

      {open && createPortal(
        <div
          className="fixed inset-0 z-[110] flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
          onClick={closeModal}
          data-testid="trading-mode-modal-backdrop"
        >
          <div
            className={`relative w-full max-w-md rounded-2xl border ${
              goingLive ? 'border-orange-500/40' : 'border-[#3DE8D9]/30'
            } bg-[#0F1A2E] p-6 shadow-2xl`}
            onClick={(e) => e.stopPropagation()}
            data-testid="trading-mode-modal"
          >
            <button
              onClick={closeModal}
              className="absolute right-4 top-4 text-slate-400 hover:text-white"
              data-testid="trading-mode-modal-close"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="flex items-center gap-3 mb-4">
              {goingLive ? (
                <div className="w-10 h-10 rounded-full bg-orange-500/15 border border-orange-500/40 flex items-center justify-center">
                  <Flame className="w-5 h-5 text-orange-300" />
                </div>
              ) : (
                <div className="w-10 h-10 rounded-full bg-[#3DE8D9]/15 border border-[#3DE8D9]/40 flex items-center justify-center">
                  <Beaker className="w-5 h-5 text-[#3DE8D9]" />
                </div>
              )}
              <div>
                <h3 className="text-white text-lg font-semibold">
                  Switch to {targetMode.toUpperCase()}
                </h3>
                <p className="text-slate-400 text-xs">
                  Currently in {mode.toUpperCase()} mode
                </p>
              </div>
            </div>

            {goingLive ? (
              <div className="space-y-4">
                <div className="rounded-lg bg-orange-500/10 border border-orange-500/30 p-3 flex gap-2">
                  <AlertTriangle className="w-4 h-4 text-orange-300 flex-shrink-0 mt-0.5" />
                  <p className="text-orange-100 text-xs leading-relaxed">
                    LIVE mode routes orders to your connected broker using
                    <span className="font-semibold"> real money</span>. Make
                    sure your broker is connected and your sizing is correct.
                  </p>
                </div>

                <div>
                  <label className="block text-slate-300 text-xs mb-1.5">
                    Type <span className="font-mono text-orange-300">LIVE</span> to confirm
                  </label>
                  <Input
                    value={confirmText}
                    onChange={(e) => setConfirmText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') e.preventDefault();
                    }}
                    placeholder="LIVE"
                    autoFocus
                    disabled={cooldown > 0 || submitting}
                    className="bg-[#0A1426] border-slate-600 text-white"
                    data-testid="trading-mode-confirm-input"
                  />
                </div>

                <div>
                  <button
                    type="button"
                    onMouseDown={startHold}
                    onMouseUp={cancelHold}
                    onMouseLeave={cancelHold}
                    onTouchStart={startHold}
                    onTouchEnd={cancelHold}
                    disabled={
                      cooldown > 0 ||
                      submitting ||
                      confirmText.trim().toUpperCase() !== 'LIVE'
                    }
                    className="relative w-full overflow-hidden rounded-lg border border-orange-500/40 bg-orange-500/10 px-4 py-3 text-orange-100 text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed select-none"
                    data-testid="trading-mode-hold-confirm"
                  >
                    <span
                      className="absolute inset-y-0 left-0 bg-orange-500/40 transition-[width] duration-75"
                      style={{ width: `${holdProgress}%` }}
                    />
                    <span className="relative z-10">
                      {holding
                        ? `Hold to confirm… ${Math.round((HOLD_MS - (holdProgress / 100) * HOLD_MS) / 1000)}s`
                        : cooldown > 0
                        ? `Cooldown ${cooldown}s`
                        : submitting
                        ? 'Switching…'
                        : 'Press & hold for 5 seconds'}
                    </span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <p className="text-slate-300 text-sm leading-relaxed">
                  PAPER mode routes all new orders to the practice ledger. Open
                  live positions stay where they are — they aren't auto-closed.
                </p>
                <Button
                  onClick={executeQuickFlip}
                  disabled={cooldown > 0 || submitting}
                  className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-slate-900 font-semibold"
                  data-testid="trading-mode-quick-confirm"
                >
                  {cooldown > 0
                    ? `Cooldown ${cooldown}s`
                    : submitting
                    ? 'Switching…'
                    : 'Switch to PAPER'}
                </Button>
              </div>
            )}

            {serverError && (
              <p
                className="text-orange-300 text-xs mt-3"
                data-testid="trading-mode-error"
              >
                {serverError}
              </p>
            )}

            {cooldown > 0 && !serverError && (
              <p className="text-slate-400 text-[11px] mt-3">
                Mode switching cools down for 30s after every flip.
              </p>
            )}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
};

export default TradingModePill;
