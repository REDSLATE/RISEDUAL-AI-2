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
    <div className="fixed inset-0 bg-black bg-opacity-80 z-50 flex items-center justify-center p-4 overflow-y-auto">
      <div className="bg-[#0a0a0b] rounded-xl max-w-4xl w-full my-8" data-testid="subscription-modal">
        {/* Header */}
        <div className="relative p-8 text-center border-b border-gray-800">
          <button
            onClick={onClose}
            className="absolute top-4 right-4 text-gray-400 hover:text-white text-2xl"
            data-testid="subscription-close-btn"
          >
            x
          </button>
          <div className="flex items-center justify-center gap-2 mb-2">
            <Crown className="w-8 h-8 text-yellow-500" />
            <h2 className="text-3xl font-bold text-white">Upgrade to Premium</h2>
          </div>
          <p className="text-gray-400 mt-2">Unlock the full power of RISEDUALAI</p>
        </div>

        {/* Pricing Card */}
        <div className="p-8">
          <div className="max-w-md mx-auto">
            <Card className="bg-gradient-to-br from-blue-900 to-purple-900 border-2 border-yellow-500 p-8 relative overflow-hidden">
              <Badge className="absolute top-4 right-4 bg-yellow-500 text-black font-bold">
                BEST VALUE
              </Badge>

              <div className="text-center mb-6">
                <div className="text-5xl font-bold text-white mb-2">
                  $50
                  <span className="text-xl text-gray-300">/year</span>
                </div>
                <p className="text-gray-300">Just $4.17/month</p>
                <div className="mt-4 inline-flex items-center gap-2 bg-green-900 bg-opacity-30 text-green-400 px-4 py-2 rounded-full text-sm">
                  <Zap className="w-4 h-4" />
                  Save 65% compared to monthly plans
                </div>
              </div>

              <div className="space-y-3">
                <Button
                  onClick={handleStripeCheckout}
                  disabled={isProcessing}
                  className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-6 text-lg"
                  data-testid="stripe-checkout-btn"
                >
                  {isProcessing ? 'Redirecting to Stripe...' : 'Subscribe Now — $50/year'}
                </Button>
              </div>

              <div className="mt-4 flex items-center justify-center gap-2 text-gray-400 text-xs">
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
                  <div key={index} className="flex items-center gap-3 text-gray-300">
                    <div className="bg-green-900 bg-opacity-30 p-2 rounded-lg">
                      <Icon className="w-5 h-5 text-green-400" />
                    </div>
                    <span>{feature.text}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* FAQ */}
          <div className="mt-8 border-t border-gray-800 pt-8">
            <h3 className="text-white text-lg font-semibold text-center mb-4">Frequently Asked Questions</h3>
            <div className="space-y-4 max-w-2xl mx-auto">
              <div>
                <p className="text-white font-medium">Can I cancel anytime?</p>
                <p className="text-gray-400 text-sm mt-1">Yes! Cancel your subscription anytime with no penalties. You'll retain access until the end of your billing period.</p>
              </div>
              <div>
                <p className="text-white font-medium">What payment methods do you accept?</p>
                <p className="text-gray-400 text-sm mt-1">We accept all major credit/debit cards securely processed via Stripe.</p>
              </div>
              <div>
                <p className="text-white font-medium">Is my payment information secure?</p>
                <p className="text-gray-400 text-sm mt-1">Absolutely! All transactions are processed securely through Stripe. We never store your payment details.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SubscriptionPricing;
