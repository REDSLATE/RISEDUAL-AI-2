import React, { useState } from 'react';
import { Check, Crown, Zap, TrendingUp, Shield, Clock } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Badge } from './ui/badge';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const SubscriptionPricing = ({ onClose }) => {
  const [isProcessing, setIsProcessing] = useState(false);

  const features = [
    { icon: TrendingUp, text: 'Real-time market data & alerts' },
    { icon: Zap, text: 'AI-powered trading insights' },
    { icon: Shield, text: 'Advanced options flow analysis' },
    { icon: Clock, text: 'Dark pool trading intelligence' },
    { icon: Crown, text: 'Direct broker integration' },
    { icon: Check, text: 'Unlimited watchlists & filters' },
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
        body: JSON.stringify({ origin_url: originUrl })
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
        {/* Sticky close button */}
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
          <p className="text-slate-400 mt-2">Unlock the full power of RISEDUALAI</p>
        </div>

        {/* Pricing Card */}
        <div className="p-8">
          <div className="max-w-md mx-auto">
            <Card className="bg-gradient-to-br from-[#0052FF]/20 to-slate-800 border border-[#0052FF]/40 rounded-2xl p-8 relative overflow-hidden">
              <Badge className="absolute top-4 right-4 bg-[#0052FF] text-white font-bold">
                BEST VALUE
              </Badge>

              <div className="text-center mb-6">
                <div className="text-5xl font-bold text-white mb-2">
                  $25
                  <span className="text-xl text-slate-300">/month</span>
                </div>
                <p className="text-slate-300">Less than $1/day</p>
                <div className="mt-4 inline-flex items-center gap-2 bg-emerald-900 bg-opacity-30 text-emerald-400 px-4 py-2 rounded-full text-sm">
                  <Zap className="w-4 h-4" />
                  Full access to all premium features
                </div>
              </div>

              <div className="space-y-3">
                <Button
                  onClick={handleStripeCheckout}
                  disabled={isProcessing}
                  className="w-full bg-[#0052FF] hover:bg-[#2563EB] text-white font-semibold py-6 text-lg"
                  data-testid="stripe-checkout-btn"
                >
                  {isProcessing ? 'Redirecting to Stripe...' : 'Subscribe Now — $25/month'}
                </Button>
              </div>

              <div className="mt-4 flex items-center justify-center gap-2 text-slate-400 text-xs">
                <Shield className="w-4 h-4" />
                <span>Secure payment via Stripe -- Cancel anytime -- 30-day money-back guarantee</span>
              </div>
            </Card>
          </div>

          {/* Features List */}
          <div className="mt-8">
            <h3 className="text-white text-xl font-semibold text-center mb-6">
              Everything You Get:
            </h3>
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
                <p className="text-white font-medium">What payment methods do you accept?</p>
                <p className="text-slate-400 text-sm mt-1">We accept all major credit/debit cards securely processed via Stripe.</p>
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
