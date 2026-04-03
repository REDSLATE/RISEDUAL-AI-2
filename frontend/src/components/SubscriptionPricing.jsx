import React, { useState } from 'react';
import { Check, Crown, Zap, TrendingUp, Shield, Clock, Star } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Badge } from './ui/badge';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const SubscriptionPricing = ({ onClose }) => {
  const [selectedPlan, setSelectedPlan] = useState('annual');
  const [isProcessing, setIsProcessing] = useState(false);

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
      console.error('Stripe checkout error:', error);
      alert('Payment processing error. Please try again.');
      setIsProcessing(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4">
      <div className="bg-slate-900 rounded-2xl max-w-4xl w-full my-4 border border-slate-700/50 relative" data-testid="subscription-modal">
        {/* Close button */}
        <button
          onClick={onClose}
          className="sticky top-2 float-right mr-4 mt-2 z-10 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-50 rounded-full w-8 h-8 flex items-center justify-center text-lg"
          data-testid="subscription-close-btn"
        >
          x
        </button>

        {/* Header */}
        <div className="p-8 pt-2 text-center border-b border-slate-700">
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
                  ? 'bg-slate-800/80 border-[#0052FF] shadow-lg shadow-blue-500/10'
                  : 'bg-slate-800/40 border-slate-700/50 hover:border-slate-600'
              }`}
              onClick={() => setSelectedPlan('monthly')}
              data-testid="plan-monthly"
            >
              <div className="flex items-center gap-3 mb-4">
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selectedPlan === 'monthly' ? 'border-[#0052FF]' : 'border-slate-600'
                }`}>
                  {selectedPlan === 'monthly' && <div className="w-2.5 h-2.5 rounded-full bg-[#0052FF]" />}
                </div>
                <span className="text-white font-semibold text-lg">Monthly</span>
              </div>
              <div className="mb-1">
                <span className="text-4xl font-bold text-white">$45</span>
                <span className="text-slate-400 text-sm">/month</span>
              </div>
              <p className="text-slate-500 text-xs">Billed monthly. Cancel anytime.</p>
            </Card>

            {/* Annual */}
            <Card
              className={`relative rounded-2xl p-6 cursor-pointer transition-all border-2 ${
                selectedPlan === 'annual'
                  ? 'bg-gradient-to-br from-[#0052FF]/15 to-slate-800/80 border-[#0052FF] shadow-lg shadow-blue-500/10'
                  : 'bg-slate-800/40 border-slate-700/50 hover:border-slate-600'
              }`}
              onClick={() => setSelectedPlan('annual')}
              data-testid="plan-annual"
            >
              <Badge className="absolute -top-2.5 right-4 bg-emerald-600 text-white font-bold text-[10px] px-2.5 py-0.5">
                SAVE 10%
              </Badge>
              <div className="flex items-center gap-3 mb-4">
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selectedPlan === 'annual' ? 'border-[#0052FF]' : 'border-slate-600'
                }`}>
                  {selectedPlan === 'annual' && <div className="w-2.5 h-2.5 rounded-full bg-[#0052FF]" />}
                </div>
                <span className="text-white font-semibold text-lg">Annual</span>
                <Star className="w-4 h-4 text-yellow-500" />
              </div>
              <div className="mb-1">
                <span className="text-4xl font-bold text-white">$40.50</span>
                <span className="text-slate-400 text-sm">/month</span>
              </div>
              <p className="text-slate-500 text-xs">
                $486/year <span className="line-through text-slate-600">$540</span>
              </p>
            </Card>
          </div>

          {/* CTA */}
          <div className="max-w-2xl mx-auto mt-6 space-y-3">
            <Button
              onClick={handleStripeCheckout}
              disabled={isProcessing}
              className="w-full bg-[#0052FF] hover:bg-[#2563EB] text-white font-semibold py-6 text-lg rounded-xl"
              data-testid="stripe-checkout-btn"
            >
              {isProcessing
                ? 'Redirecting to Stripe...'
                : selectedPlan === 'annual'
                  ? 'Subscribe Now — $486/year'
                  : 'Subscribe Now — $45/month'
              }
            </Button>
            <div className="flex items-center justify-center gap-2 text-slate-400 text-xs">
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
                  <div key={index} className="flex items-center gap-3 text-slate-300">
                    <div className="bg-emerald-900 bg-opacity-30 p-2 rounded-lg">
                      <Icon className="w-5 h-5 text-emerald-400" />
                    </div>
                    <span>{feature.text}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* FAQ */}
          <div className="mt-8 border-t border-slate-700 pt-8">
            <h3 className="text-white text-lg font-semibold text-center mb-4">Frequently Asked Questions</h3>
            <div className="space-y-4 max-w-2xl mx-auto">
              <div>
                <p className="text-white font-medium">Can I cancel anytime?</p>
                <p className="text-slate-400 text-sm mt-1">Yes! Cancel your subscription anytime with no penalties. You'll retain access until the end of your billing period.</p>
              </div>
              <div>
                <p className="text-white font-medium">Can I switch between monthly and annual?</p>
                <p className="text-slate-400 text-sm mt-1">Yes, you can switch plans at any time. If upgrading to annual, you'll receive prorated credit for your remaining monthly period.</p>
              </div>
              <div>
                <p className="text-white font-medium">Is my payment information secure?</p>
                <p className="text-slate-400 text-sm mt-1">Absolutely! All transactions are processed securely through Stripe. We never store your payment details.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SubscriptionPricing;
