import React, { useState } from 'react';
import { Check, Crown, Zap, TrendingUp, Shield, Clock, Star } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { toast } from './ui/sonner';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const SubscriptionPricing = ({ onClose }) => {
  const [selectedPlan, setSelectedPlan] = useState('annual');
  const [isProcessing, setIsProcessing] = useState(false);
  const [riskAccepted, setRiskAccepted] = useState(false);

  const features = [
    { icon: TrendingUp, text: 'Real-time market data & alerts' },
    { icon: Zap, text: 'AI-powered trading insights' },
    { icon: Shield, text: 'Advanced options flow analysis' },
    { icon: Clock, text: 'Dark pool trading intelligence' },
    { icon: Crown, text: 'Direct broker integration' },
    { icon: Check, text: 'Macro Intelligence dashboard' },
    { icon: Check, text: 'Export data to CSV' },
    { icon: Check, text: 'Priority customer support' },
  ];

  const handleStripeCheckout = async () => {
    setIsProcessing(true);
    try {
      const originUrl = window.location.origin;
      const response = await fetch(`${API}/subscription/create-checkout-session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ origin_url: originUrl, plan: selectedPlan })
      });

      if (!response.ok) throw new Error('Failed to create checkout session');
      const data = await response.json();

      if (data.url) {
        window.location.href = data.url;
      } else {
        throw new Error('No checkout URL received');
      }
    } catch (error) {
      logger.error('Stripe checkout error:', error);
      alert('Payment processing error. Please try again.');
      setIsProcessing(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4">
      <div className="bg-slate-900 rounded-2xl max-w-4xl w-full my-4 border border-slate-400/25 relative" data-testid="subscription-modal">
        {/* Close button */}
        <button
          onClick={onClose}
          className="sticky top-2 float-right mr-4 mt-2 z-10 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-50 rounded-full w-8 h-8 flex items-center justify-center text-lg"
          data-testid="subscription-close-btn"
        >
          x
        </button>

        {/* Header */}
        <div className="p-8 pt-2 text-center border-b border-slate-400/30">
          <div className="flex items-center justify-center gap-2 mb-2">
            <Crown className="w-8 h-8 text-yellow-500" />
            <h2 className="text-3xl font-bold text-white">Upgrade to Premium</h2>
          </div>
          <p className="text-slate-400 mt-2">Unlock the full power of RISEDUAL AI</p>
        </div>

        {/* Pricing Cards */}
        <div className="p-8">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 max-w-2xl mx-auto">
            {/* Monthly */}
            <Card
              className={`relative rounded-2xl p-6 cursor-pointer transition-all border-2 ${
                selectedPlan === 'monthly'
                  ? 'bg-slate-800/80 border-[#3DE8D9] shadow-lg shadow-blue-500/10'
                  : 'bg-slate-700/40 border-slate-400/25 hover:border-slate-600'
              }`}
              onClick={() => setSelectedPlan('monthly')}
              data-testid="plan-monthly"
            >
              <div className="flex items-center gap-3 mb-4">
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selectedPlan === 'monthly' ? 'border-[#3DE8D9]' : 'border-slate-600'
                }`}>
                  {selectedPlan === 'monthly' && <div className="w-2.5 h-2.5 rounded-full bg-[#3DE8D9]" />}
                </div>
                <span className="text-white font-semibold text-lg">Monthly</span>
              </div>
              <div className="mb-1">
                <span className="text-4xl font-bold text-white">$55</span>
                <span className="text-slate-300 text-sm">/month</span>
              </div>
              <p className="text-slate-300 text-xs">Billed monthly. Cancel anytime.</p>
            </Card>

            {/* Annual */}
            <Card
              className={`relative rounded-2xl p-6 cursor-pointer transition-all border-2 ${
                selectedPlan === 'annual'
                  ? 'bg-gradient-to-br from-[#3DE8D9]/15 to-slate-800/80 border-[#3DE8D9] shadow-lg shadow-blue-500/10'
                  : 'bg-slate-700/40 border-slate-400/25 hover:border-slate-600'
              }`}
              onClick={() => setSelectedPlan('annual')}
              data-testid="plan-annual"
            >
              <Badge className="absolute -top-2.5 right-4 bg-green-600 text-white font-bold text-[10px] px-2.5 py-0.5">
                SAVE 10%
              </Badge>
              <div className="flex items-center gap-3 mb-4">
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selectedPlan === 'annual' ? 'border-[#3DE8D9]' : 'border-slate-600'
                }`}>
                  {selectedPlan === 'annual' && <div className="w-2.5 h-2.5 rounded-full bg-[#3DE8D9]" />}
                </div>
                <span className="text-white font-semibold text-lg">Annual</span>
                <Star className="w-4 h-4 text-yellow-500" />
              </div>
              <div className="mb-1">
                <span className="text-4xl font-bold text-white">$49.50</span>
                <span className="text-slate-300 text-sm">/month</span>
              </div>
              <p className="text-slate-300 text-xs">
                $594/year <span className="line-through text-slate-400">$660</span>
              </p>
            </Card>
          </div>

          {/* Risk Disclosure Checkbox */}
          <div className="max-w-2xl mx-auto mt-6">
            <div className="bg-amber-900/15 border border-amber-700/30 rounded-xl p-4 mb-4" data-testid="checkout-risk-disclosure">
              <p className="text-amber-400 text-[10px] font-bold uppercase tracking-wider mb-2">Investment Risk Disclosure</p>
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
          <div className="max-w-2xl mx-auto space-y-3">
            <Button
              onClick={handleStripeCheckout}
              disabled={isProcessing || !riskAccepted}
              className="w-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white font-semibold py-6 text-lg rounded-xl"
              data-testid="stripe-checkout-btn"
            >
              {isProcessing
                ? 'Redirecting to Stripe...'
                : selectedPlan === 'annual'
                  ? 'Subscribe Now — $594/year'
                  : 'Subscribe Now — $55/month'
              }
            </Button>
            <div className="flex items-center justify-center gap-2 text-slate-300 text-xs">
              <Shield className="w-4 h-4" />
              <span>Secure payment via Stripe -- Cancel anytime -- 30-day money-back guarantee</span>
            </div>
          </div>

          {/* Features List */}
          <div className="mt-8">
            <h3 className="text-white text-xl font-semibold text-center mb-6">Everything You Get:</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-3xl mx-auto">
              {features.map((feature, index) => {
                const Icon = feature.icon;
                return (
                  <div key={`feature-${feature.text}`} className="flex items-center gap-3 text-slate-300">
                    <div className="bg-emerald-900 bg-opacity-30 p-2 rounded-lg">
                      <Icon className="w-5 h-5 text-lime-400" />
                    </div>
                    <span>{feature.text}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* FAQ */}
          <div className="mt-8 border-t border-slate-400/30 pt-8">
            <h3 className="text-white text-lg font-semibold text-center mb-4">Frequently Asked Questions</h3>
            <div className="space-y-4 max-w-2xl mx-auto">
              <div>
                <p className="text-white font-medium">Can I cancel anytime?</p>
                <p className="text-slate-300 text-sm mt-1">Yes! Cancel your subscription anytime with no penalties. You'll retain access until the end of your billing period.</p>
              </div>
              <div>
                <p className="text-white font-medium">Can I switch between monthly and annual?</p>
                <p className="text-slate-300 text-sm mt-1">Yes, you can switch plans at any time. If upgrading to annual, you'll receive prorated credit for your remaining monthly period.</p>
              </div>
              <div>
                <p className="text-white font-medium">Is my payment information secure?</p>
                <p className="text-slate-300 text-sm mt-1">Absolutely! All transactions are processed securely through Stripe. We never store your payment details.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SubscriptionPricing;
