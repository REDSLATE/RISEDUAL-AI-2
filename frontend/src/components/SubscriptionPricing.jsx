import React, { useState } from 'react';
import { Check, Crown, Zap, TrendingUp, Shield, Clock, Star, Sparkles } from 'lucide-react';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { toast } from './ui/sonner';
import logger from '../utils/logger';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

// ── Plan catalog ──────────────────────────────────────────────────────────────
// Single source of truth for the modal. Each plan declares its monthly +
// annual price IDs (mapped to backend plan keys), display prices, and
// the savings copy shown when Annual is toggled. Prices match
// /app/backend/.env: STRIPE_PRICE_* and what's wired in
// services/stripe_billing_service.py PLAN_PRICE_IDS.
const PLANS = [
  {
    id: 'starter',
    name: 'Starter',
    monthly: { price: 19, key: 'starter', label: '$19/mo' },
    // No annual SKU yet — Annual toggle disables this card.
    annual: null,
    perks: [
      '3,000 AI credits / month',
      'Real-time market data',
      'Watchlist & alerts',
      'Standard chat support',
    ],
    accent: 'slate',
  },
  {
    id: 'pro',
    name: 'Pro',
    monthly: { price: 55, key: 'pro', label: '$55/mo' },
    annual:  { price: 594, perMonth: 49.5, key: 'pro_annual', label: '$594/yr' },
    perks: [
      '15,000 AI credits / month',
      'Unlimited chat & AI War Room',
      'Options flow & dark pool data',
      'AI predictions + journal',
      'Priority support',
    ],
    accent: 'cyan',
  },
  {
    id: 'pro_max',
    name: 'Pro Max',
    monthly: { price: 99, key: 'pro_max', label: '$99/mo' },
    annual:  { price: 1068, perMonth: 89, key: 'pro_max_annual', label: '$1,068/yr' },
    perks: [
      '50,000 AI credits / month',
      'Everything in Pro',
      'Top-up at $5 / 1K credits',
      'Direct broker integration',
      'Macro Intelligence dashboard',
      'White-glove onboarding',
    ],
    accent: 'gold',
    featured: true,
  },
];

const FEATURE_HIGHLIGHTS = [
  { icon: TrendingUp, text: 'Real-time market data & alerts' },
  { icon: Zap,        text: 'AI-powered trading insights' },
  { icon: Shield,     text: 'Advanced options flow analysis' },
  { icon: Clock,      text: 'Dark pool trading intelligence' },
  { icon: Crown,      text: 'Direct broker integration' },
  { icon: Check,      text: 'Macro Intelligence dashboard' },
];


// ── Plan card ────────────────────────────────────────────────────────────────


const PlanCard = ({ plan, billing, isSelected, onSelect, disabled }) => {
  const { name, monthly, annual, perks, accent, featured, id } = plan;
  const tier = billing === 'annual' ? annual : monthly;

  // Card border + glow style varies by accent.
  const accentClass = featured
    ? 'bg-gradient-to-br from-amber-500/10 via-slate-800/80 to-slate-800/80 border-amber-400'
    : accent === 'cyan'
      ? 'bg-slate-800/70 border-[#3DE8D9]'
      : 'bg-slate-800/50 border-slate-600';

  const idleClass = 'bg-slate-700/30 border-slate-700 hover:border-slate-500';

  if (!tier) {
    // Annual variant unavailable — show greyed-out state.
    return (
      <div
        className="rounded-2xl p-6 border-2 border-dashed border-slate-700 bg-slate-900/40 text-center opacity-60"
        data-testid={`plan-${id}-unavailable`}
      >
        <h3 className="text-white font-bold text-xl mb-2">{name}</h3>
        <p className="text-slate-400 text-sm">Annual billing not yet available.</p>
        <p className="text-slate-500 text-xs mt-1">Switch to Monthly to subscribe.</p>
      </div>
    );
  }

  const monthlyPrice = billing === 'annual' ? tier.perMonth : tier.price;
  const subline = billing === 'annual'
    ? `${tier.label} · billed yearly`
    : 'Billed monthly · cancel anytime';

  return (
    <button
      type="button"
      onClick={() => !disabled && onSelect(id)}
      disabled={disabled}
      className={`relative rounded-2xl p-6 text-left border-2 transition-all w-full ${
        isSelected ? accentClass + ' shadow-lg shadow-cyan-500/10' : idleClass
      } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
      data-testid={`plan-${id}`}
    >
      {featured && (
        <Badge
          className="absolute -top-2.5 right-4 bg-amber-500 text-black font-bold text-[10px] px-2.5 py-0.5"
          data-testid={`plan-${id}-featured-badge`}
        >
          MOST POPULAR
        </Badge>
      )}
      <div className="flex items-center gap-2 mb-3">
        {featured && <Sparkles className="w-4 h-4 text-amber-400" />}
        <h3 className="text-white font-bold text-xl">{name}</h3>
      </div>
      <div className="mb-1">
        <span className="text-4xl font-bold text-white">${monthlyPrice}</span>
        <span className="text-slate-300 text-sm">/mo</span>
      </div>
      <p className="text-slate-400 text-xs mb-4">{subline}</p>

      <ul className="space-y-2 mb-2">
        {perks.map((perk) => (
          <li key={perk} className="flex items-start gap-2 text-slate-300 text-sm">
            <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <span>{perk}</span>
          </li>
        ))}
      </ul>
    </button>
  );
};


// ── Main modal ───────────────────────────────────────────────────────────────


const SubscriptionPricing = ({ onClose }) => {
  const [billing, setBilling] = useState('annual');     // 'monthly' | 'annual'
  const [selectedId, setSelectedId] = useState('pro_max');
  const [isProcessing, setIsProcessing] = useState(false);
  const [riskAccepted, setRiskAccepted] = useState(false);

  const selectedPlan = PLANS.find((p) => p.id === selectedId);
  const selectedTier = selectedPlan
    ? (billing === 'annual' ? selectedPlan.annual : selectedPlan.monthly)
    : null;

  // Auto-fall-back: if user toggles to Annual while Starter (no annual) is
  // selected, bump them to Pro so the CTA stays clickable.
  React.useEffect(() => {
    if (billing === 'annual' && selectedPlan && !selectedPlan.annual) {
      setSelectedId('pro');
    }
  }, [billing, selectedPlan]);

  const handleStripeCheckout = async () => {
    if (!selectedTier) {
      toast.error('Please pick a plan.');
      return;
    }
    setIsProcessing(true);
    try {
      const response = await authFetch(`${API}/billing/checkout/subscription`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan: selectedTier.key }),
      });
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to create checkout session');
      }
      const data = await response.json();
      const url = data.checkout_url || data.url;
      if (url) {
        window.location.href = url;
      } else {
        throw new Error('No checkout URL received');
      }
    } catch (error) {
      logger.error('Stripe checkout error:', error);
      toast.error(error.message || 'Payment processing error. Please try again.');
      setIsProcessing(false);
    }
  };

  const ctaLabel = isProcessing
    ? 'Redirecting to Stripe...'
    : selectedTier
      ? `Subscribe — ${selectedTier.label}`
      : 'Pick a plan';

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4">
      <div
        className="bg-slate-900 rounded-2xl max-w-5xl w-full my-4 border border-slate-400/25 relative"
        data-testid="subscription-modal"
      >
        {/* Close */}
        <button
          onClick={onClose}
          className="sticky top-2 float-right mr-4 mt-2 z-10 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-50 rounded-full w-8 h-8 flex items-center justify-center text-lg"
          data-testid="subscription-close-btn"
        >
          x
        </button>

        {/* Header */}
        <div className="p-8 pt-6 text-center border-b border-slate-400/30">
          <div className="flex items-center justify-center gap-2 mb-2">
            <Crown className="w-8 h-8 text-yellow-500" />
            <h2 className="text-3xl font-bold text-white">Choose Your Plan</h2>
          </div>
          <p className="text-slate-400 mt-2">Unlock the full power of RISEDUAL AI</p>
        </div>

        <div className="p-8">
          {/* Billing-interval toggle */}
          <div className="flex justify-center mb-8">
            <div
              className="inline-flex rounded-full bg-slate-800/80 border border-slate-700 p-1"
              data-testid="billing-toggle"
            >
              <button
                type="button"
                onClick={() => setBilling('monthly')}
                className={`px-5 py-2 rounded-full text-sm font-medium transition-all ${
                  billing === 'monthly'
                    ? 'bg-slate-700 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
                data-testid="billing-toggle-monthly"
              >
                Monthly
              </button>
              <button
                type="button"
                onClick={() => setBilling('annual')}
                className={`px-5 py-2 rounded-full text-sm font-medium transition-all flex items-center gap-2 ${
                  billing === 'annual'
                    ? 'bg-[#3DE8D9] text-slate-900'
                    : 'text-slate-400 hover:text-white'
                }`}
                data-testid="billing-toggle-annual"
              >
                Annual
                <Badge className="bg-green-600 text-white text-[10px] px-2 py-0">
                  SAVE 10%
                </Badge>
              </button>
            </div>
          </div>

          {/* Plan cards — 3 across on md+, stacked on mobile */}
          <div
            className="grid grid-cols-1 md:grid-cols-3 gap-5"
            data-testid="plan-grid"
          >
            {PLANS.map((plan) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                billing={billing}
                isSelected={selectedId === plan.id}
                onSelect={setSelectedId}
                disabled={billing === 'annual' && !plan.annual}
              />
            ))}
          </div>

          {/* Risk Disclosure */}
          <div className="max-w-3xl mx-auto mt-8">
            <div
              className="bg-amber-900/15 border border-amber-700/30 rounded-xl p-4 mb-4"
              data-testid="checkout-risk-disclosure"
            >
              <p className="text-amber-400 text-[10px] font-bold uppercase tracking-wider mb-2">
                Investment Risk Disclosure
              </p>
              <ul className="space-y-1 text-slate-400 text-[10px] leading-relaxed mb-3">
                <li><strong className="text-slate-300">High Risk Warning:</strong> Trading stocks, options, and digital assets involves significant risk of loss.</li>
                <li><strong className="text-slate-300">No Financial Advice:</strong> RISEDUAL AI is a financial research publishing platform. All content is for informational and educational purposes only.</li>
                <li><strong className="text-slate-300">Not a Broker/Adviser:</strong> We are not registered investment advisers (RIAs) or broker-dealers.</li>
                <li><strong className="text-slate-300">AI Limitations:</strong> AI can "hallucinate" or provide inaccurate data. Perform your own due diligence.</li>
                <li><strong className="text-slate-300">Past Performance:</strong> Backtests or historical results are not indicative of future performance.</li>
              </ul>
              <label className="flex items-start gap-2.5 cursor-pointer group" data-testid="risk-checkbox-label">
                <input
                  type="checkbox"
                  checked={riskAccepted}
                  onChange={(e) => setRiskAccepted(e.target.checked)}
                  className="mt-0.5 w-4 h-4 rounded border-slate-600 bg-slate-800 text-[#3DE8D9] focus:ring-[#3DE8D9] focus:ring-offset-0 shrink-0"
                  data-testid="risk-checkbox"
                />
                <span className="text-slate-300 text-[10px] leading-relaxed group-hover:text-white transition-colors">
                  I have read and understand the Investment Risk Disclosure. I acknowledge that trading involves significant risk of loss and that RISEDUAL AI does not provide financial advice.
                </span>
              </label>
            </div>
          </div>

          {/* CTA */}
          <div className="max-w-3xl mx-auto space-y-3">
            <Button
              onClick={handleStripeCheckout}
              disabled={isProcessing || !riskAccepted || !selectedTier}
              className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-slate-900 font-semibold py-6 text-lg rounded-xl"
              data-testid="stripe-checkout-btn"
            >
              {ctaLabel}
            </Button>
            <div className="flex items-center justify-center gap-2 text-slate-300 text-xs">
              <Shield className="w-4 h-4" />
              <span>Secure payment via Stripe — Cancel anytime — 30-day money-back guarantee</span>
            </div>
          </div>

          {/* Feature highlights */}
          <div className="mt-10">
            <h3 className="text-white text-xl font-semibold text-center mb-6">
              Everything You Get
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-3xl mx-auto">
              {FEATURE_HIGHLIGHTS.map(({ icon: Icon, text }) => (
                <div key={text} className="flex items-center gap-3 text-slate-300">
                  <div className="bg-emerald-900 bg-opacity-30 p-2 rounded-lg">
                    <Icon className="w-5 h-5 text-lime-400" />
                  </div>
                  <span>{text}</span>
                </div>
              ))}
            </div>
          </div>

          {/* FAQ */}
          <div className="mt-10 border-t border-slate-400/30 pt-8">
            <h3 className="text-white text-lg font-semibold text-center mb-4">
              Frequently Asked Questions
            </h3>
            <div className="space-y-4 max-w-2xl mx-auto">
              <div>
                <p className="text-white font-medium">Can I cancel anytime?</p>
                <p className="text-slate-300 text-sm mt-1">
                  Yes — cancel anytime with no penalties. You retain access until the end of your billing period.
                </p>
              </div>
              <div>
                <p className="text-white font-medium">Can I switch between monthly and annual?</p>
                <p className="text-slate-300 text-sm mt-1">
                  Yes. Open the Customer Portal from your account page to change plans. Stripe prorates the difference automatically.
                </p>
              </div>
              <div>
                <p className="text-white font-medium">Is my payment information secure?</p>
                <p className="text-slate-300 text-sm mt-1">
                  All transactions are processed through Stripe. We never store payment details on our servers.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SubscriptionPricing;
