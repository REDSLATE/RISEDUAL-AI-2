import React, { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, Circle, ArrowRight, Rocket, Building2, Play, X } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';
import { toast } from './ui/sonner';

/**
 * PostSignupOnboarding — 3-step guided flow that fires ONCE after a
 * fresh signup so nobody hits a dead-end after account creation.
 *
 * Step 1: Welcome (30-second why-we're-here).
 * Step 2: Connect a broker — live broker status pulled from
 *          `/api/onboarding/status`, with a jump-off into the existing
 *          BrokerConnect modal (dispatched via a window event so we
 *          don't have to hoist BrokerConnect's imperative API).
 * Step 3: Ready — with a "Take the platform tour" button that starts
 *          the existing 10-step OnboardingTour.
 *
 * State: persists completion server-side via POST /api/onboarding/complete.
 * The modal is rendered by AuthenticatedShell and controlled through
 * `onboarding.completed` on the user object.
 */
const PostSignupOnboarding = ({ user, onDismiss, onStartPlatformTour }) => {
  const [step, setStep] = useState(0);
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const r = await fetch(`${getApiBase()}/api/onboarding/status`, {
        credentials: 'include',
      });
      if (r.ok) {
        const data = await r.json();
        setStatus(data);
      }
    } catch (_) {
      // best effort — the modal still opens on failure
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const persist = async (stage) => {
    setSaving(true);
    try {
      await fetch(`${getApiBase()}/api/onboarding/complete`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage: stage || 'completed' }),
      });
    } catch (_) {
      // best effort — dismissing still works locally
    } finally {
      setSaving(false);
    }
  };

  const handleFinish = async () => {
    await persist('completed');
    onDismiss?.();
  };

  const handleSkip = async () => {
    await persist('skipped');
    onDismiss?.();
    toast('You can revisit onboarding from the Help menu anytime.');
  };

  const openBrokerConnect = () => {
    // Dispatched — Navbar's BrokerConnect listens for this and opens.
    // Falls back to a scroll if the listener isn't wired.
    window.dispatchEvent(new CustomEvent('risedual:open-broker-connect'));
  };

  const brokers = status?.brokers || {};
  const anyConnected = brokers.any_connected;

  const STEPS = [
    {
      title: `Welcome, ${user?.name?.split(' ')[0] || 'trader'}.`,
      icon: Rocket,
      body: (
        <div className="space-y-3 text-sm text-slate-300 leading-relaxed">
          <p>
            RISEDUAL AI runs an <span className="text-teal-300 font-medium">adversarial trading loop</span>{' '}
            &mdash; four AI runtimes debate every trade before Alpha routes it live.
          </p>
          <p>
            You&rsquo;re two clicks away from a functional trading dashboard. Let&rsquo;s make sure a
            broker is wired first so Alpha has somewhere to route.
          </p>
          <ul className="text-xs text-slate-400 space-y-1 mt-4 border-l-2 border-teal-500/30 pl-3">
            <li>&bull; Alpha proposes trades from the live market scanner</li>
            <li>&bull; Camaro, Chevelle, and RedEye challenge, audit, and argue the other side</li>
            <li>&bull; Only trades that survive all four reach your broker</li>
          </ul>
        </div>
      ),
    },
    {
      title: 'Connect a broker',
      icon: Building2,
      body: (
        <div className="space-y-4">
          <p className="text-sm text-slate-300">
            Pick whichever venue you already trade on. Both integrations are read + write
            &mdash; positions sync, orders route.
          </p>
          <div className="space-y-2">
            <BrokerRow
              name="Public.com"
              subtitle="Equities · fractional shares"
              connected={brokers.public_connected}
              testid="onboarding-broker-public"
            />
            <BrokerRow
              name="MooMoo"
              subtitle="Equities + options · Level 2 data"
              connected={brokers.moomoo_connected}
              testid="onboarding-broker-moomoo"
            />
          </div>
          <button
            type="button"
            onClick={openBrokerConnect}
            data-testid="onboarding-open-broker-connect"
            className="w-full mt-2 px-4 py-3 rounded-lg bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold text-sm flex items-center justify-center gap-2 hover:opacity-90 transition"
          >
            <Building2 className="w-4 h-4" />
            {anyConnected ? 'Manage broker connections' : 'Connect a broker'}
            <ArrowRight className="w-4 h-4" />
          </button>
          <p className="text-[11px] text-slate-500 text-center">
            No API keys yet? You can keep exploring the demo &mdash; Alpha stays paused
            until a broker is connected.
          </p>
        </div>
      ),
    },
    {
      title: 'You&rsquo;re set',
      icon: CheckCircle2,
      body: (
        <div className="space-y-4">
          <p className="text-sm text-slate-300">
            {anyConnected
              ? 'Your broker is wired. Alpha will only route trades that survive the adversarial loop, so nothing goes live until we all agree.'
              : 'You can revisit broker setup anytime from the top-right menu. In the meantime, explore the platform.'}
          </p>
          <div className="rounded-lg border border-teal-500/20 bg-teal-500/5 p-3">
            <div className="text-xs uppercase tracking-wider text-teal-400 mb-1">Next up</div>
            <div className="text-sm text-slate-200">
              A 60-second guided tour of the dashboard, so you know where everything lives.
            </div>
          </div>
          <button
            type="button"
            onClick={async () => {
              await persist('tour_started');
              onDismiss?.();
              onStartPlatformTour?.();
            }}
            data-testid="onboarding-start-tour"
            className="w-full px-4 py-3 rounded-lg bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold text-sm flex items-center justify-center gap-2 hover:opacity-90 transition"
          >
            <Play className="w-4 h-4" /> Take the tour <ArrowRight className="w-4 h-4" />
          </button>
          <button
            type="button"
            onClick={handleFinish}
            data-testid="onboarding-finish"
            className="w-full px-4 py-2.5 rounded-lg border border-slate-700 text-slate-300 text-sm hover:bg-slate-800 transition"
          >
            Skip the tour &mdash; take me to the dashboard
          </button>
        </div>
      ),
    },
  ];

  const total = STEPS.length;
  const current = STEPS[step];
  const Icon = current.icon;

  return (
    <div
      data-testid="post-signup-onboarding"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/85 backdrop-blur-sm p-4"
    >
      <div className="relative w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
        <button
          type="button"
          onClick={handleSkip}
          disabled={saving}
          data-testid="onboarding-skip"
          className="absolute top-3 right-3 text-slate-500 hover:text-slate-300 transition disabled:opacity-40"
          aria-label="Skip onboarding"
        >
          <X className="w-5 h-5" />
        </button>
        <div className="px-6 pt-6 pb-2 flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-teal-500/10 border border-teal-500/40 flex items-center justify-center">
            <Icon className="w-5 h-5 text-teal-400" />
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-500">
              Step {step + 1} of {total}
            </div>
            <h2
              className="text-lg font-semibold text-white"
              data-testid="onboarding-step-title"
              dangerouslySetInnerHTML={{ __html: current.title }}
            />
          </div>
        </div>
        <div className="px-6 pb-4 pt-2">
          {loading ? (
            <div className="text-sm text-slate-400">Loading your account status…</div>
          ) : current.body}
        </div>
        <div className="px-6 py-4 border-t border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            {STEPS.map((_, i) => (
              <div
                key={i}
                className={`h-1.5 rounded-full transition-all ${
                  i === step ? 'w-6 bg-teal-400' : (i < step ? 'w-4 bg-teal-700' : 'w-4 bg-slate-700')
                }`}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            {step > 0 ? (
              <button
                type="button"
                onClick={() => setStep(step - 1)}
                data-testid="onboarding-back"
                className="text-xs text-slate-400 hover:text-white px-2 py-1"
              >
                Back
              </button>
            ) : null}
            {step < total - 1 ? (
              <button
                type="button"
                onClick={() => setStep(step + 1)}
                data-testid="onboarding-next"
                className="text-xs px-4 py-2 rounded-full bg-teal-500 hover:bg-teal-400 text-white font-semibold flex items-center gap-1"
              >
                Continue <ArrowRight className="w-3 h-3" />
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
};

const BrokerRow = ({ name, subtitle, connected, testid }) => (
  <div
    data-testid={testid}
    className={`flex items-center justify-between rounded-lg border px-3 py-2.5 ${
      connected
        ? 'border-emerald-600/60 bg-emerald-600/5'
        : 'border-slate-800 bg-slate-950/60'
    }`}
  >
    <div>
      <div className="text-sm font-medium text-white">{name}</div>
      <div className="text-[11px] text-slate-500">{subtitle}</div>
    </div>
    {connected ? (
      <div className="flex items-center gap-1.5 text-emerald-400 text-xs font-medium">
        <CheckCircle2 className="w-4 h-4" /> Connected
      </div>
    ) : (
      <div className="flex items-center gap-1.5 text-slate-500 text-xs">
        <Circle className="w-4 h-4" /> Not connected
      </div>
    )}
  </div>
);

export default PostSignupOnboarding;
