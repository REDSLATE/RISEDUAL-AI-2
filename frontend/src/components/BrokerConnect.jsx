import React, { useState } from 'react';
import { Building2, CheckCircle, ExternalLink, ArrowRight, Shield } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Badge } from './ui/badge';

const BrokerConnect = () => {
  const [connectedBrokers, setConnectedBrokers] = useState([]);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const brokers = [
    {
      id: 'td_ameritrade',
      name: 'TD Ameritrade',
      logo: '🏦',
      description: 'Commission-free trades with advanced tools',
      features: ['Real-time data', 'Options trading', 'Paper trading'],
      apiDocs: 'https://developer.tdameritrade.com',
      connectUrl: 'https://www.tdameritrade.com',
      popular: true
    },
    {
      id: 'interactive_brokers',
      name: 'Interactive Brokers',
      logo: '🌐',
      description: 'Professional trading platform with global access',
      features: ['Low commissions', 'Global markets', 'API access'],
      apiDocs: 'https://www.interactivebrokers.com/api',
      connectUrl: 'https://www.interactivebrokers.com',
      popular: true
    },
    {
      id: 'robinhood',
      name: 'Robinhood',
      logo: '🎯',
      description: 'Simple commission-free investing',
      features: ['Zero commissions', 'Crypto trading', 'Easy to use'],
      apiDocs: 'https://robinhood.com/us/en/support/articles/api/',
      connectUrl: 'https://robinhood.com',
      popular: true
    },
    {
      id: 'etrade',
      name: 'E*TRADE',
      logo: '💼',
      description: 'Comprehensive trading and investing platform',
      features: ['Research tools', 'Retirement accounts', 'Mobile app'],
      apiDocs: 'https://developer.etrade.com',
      connectUrl: 'https://www.etrade.com',
      popular: false
    },
    {
      id: 'schwab',
      name: 'Charles Schwab',
      logo: '🏛️',
      description: 'Full-service brokerage with excellent research',
      features: ['No commissions', 'Banking services', 'Robo-advisor'],
      apiDocs: 'https://developer.schwab.com',
      connectUrl: 'https://www.schwab.com',
      popular: false
    },
    {
      id: 'fidelity',
      name: 'Fidelity',
      logo: '🏢',
      description: 'Trusted broker with extensive resources',
      features: ['Research', 'Retirement planning', 'No fees'],
      apiDocs: 'https://www.fidelity.com/bin-public/060_www_fidelity_com/documents/landing-zones/Fidelity-WealthscapeIntegration-Guide.pdf',
      connectUrl: 'https://www.fidelity.com',
      popular: false
    },
    {
      id: 'webull',
      name: 'Webull',
      logo: '📱',
      description: 'Mobile-first platform with advanced charts',
      features: ['Paper trading', 'Extended hours', 'Free tools'],
      apiDocs: 'https://www.webull.com',
      connectUrl: 'https://www.webull.com',
      popular: false
    },
    {
      id: 'alpaca',
      name: 'Alpaca',
      logo: '🦙',
      description: 'Commission-free API-first broker for algo trading',
      features: ['API trading', 'Paper trading', 'No minimums'],
      apiDocs: 'https://alpaca.markets/docs',
      connectUrl: 'https://alpaca.markets',
      popular: true
    }
  ];

  const handleConnect = (broker) => {
    // In production, this would initiate OAuth flow
    const isConnected = connectedBrokers.includes(broker.id);
    
    if (isConnected) {
      setConnectedBrokers(connectedBrokers.filter(id => id !== broker.id));
      alert(`Disconnected from ${broker.name}`);
    } else {
      // Open broker's website in new tab
      window.open(broker.connectUrl, '_blank');
      // Simulate connection (in production, this would happen after OAuth)
      setConnectedBrokers([...connectedBrokers, broker.id]);
    }
  };

  const BrokerCard = ({ broker }) => {
    const isConnected = connectedBrokers.includes(broker.id);

    return (
      <Card className="bg-[#1a1a1b] border-gray-800 p-5 hover:border-gray-700 transition-all">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className="text-4xl">{broker.logo}</div>
            <div>
              <div className="flex items-center gap-2">
                <h4 className="text-white font-semibold">{broker.name}</h4>
                {broker.popular && (
                  <Badge className="bg-blue-600 text-xs">Popular</Badge>
                )}
                {isConnected && (
                  <Badge className="bg-green-600 text-xs flex items-center gap-1">
                    <CheckCircle className="w-3 h-3" />
                    Connected
                  </Badge>
                )}
              </div>
              <p className="text-gray-400 text-sm mt-1">{broker.description}</p>
            </div>
          </div>
        </div>

        {/* Features */}
        <div className="flex flex-wrap gap-2 mb-4">
          {broker.features.map((feature, index) => (
            <span
              key={index}
              className="bg-[#272729] text-gray-300 text-xs px-2 py-1 rounded"
            >
              {feature}
            </span>
          ))}
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          <Button
            onClick={() => handleConnect(broker)}
            className={`flex-1 ${
              isConnected
                ? 'bg-gray-700 hover:bg-gray-600'
                : 'bg-blue-600 hover:bg-blue-700'
            }`}
          >
            {isConnected ? 'Disconnect' : 'Connect Account'}
            {!isConnected && <ArrowRight className="w-4 h-4 ml-2" />}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.open(broker.apiDocs, '_blank')}
            className="bg-[#272729] border-gray-700 text-white hover:bg-[#2a2a2c]"
          >
            API Docs
            <ExternalLink className="w-3 h-3 ml-1" />
          </Button>
        </div>
      </Card>
    );
  };

  return (
    <>
      {/* Trigger Button */}
      <Button
        onClick={() => setIsModalOpen(true)}
        className="bg-blue-600 hover:bg-blue-700"
      >
        <Building2 className="w-4 h-4 mr-2" />
        Connect Broker
        {connectedBrokers.length > 0 && (
          <Badge className="ml-2 bg-green-600">{connectedBrokers.length}</Badge>
        )}
      </Button>

      {/* Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black bg-opacity-70 z-50 flex items-center justify-center p-4">
          <div className="bg-[#0f0f10] rounded-lg max-w-6xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Header */}
            <div className="border-b border-gray-800 p-6">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-white text-2xl font-bold mb-2">Connect Your Broker</h2>
                  <p className="text-gray-400">
                    Link your brokerage account to execute trades directly from RISEDUALAI
                  </p>
                  <div className="flex items-center gap-2 mt-3 text-sm text-gray-500">
                    <Shield className="w-4 h-4" />
                    <span>Bank-level security • OAuth 2.0 • Data encrypted</span>
                  </div>
                </div>
                <button
                  onClick={() => setIsModalOpen(false)}
                  className="text-gray-400 hover:text-white text-2xl"
                >
                  ×
                </button>
              </div>
            </div>

            {/* Connected Brokers Summary */}
            {connectedBrokers.length > 0 && (
              <div className="bg-green-900 bg-opacity-20 border-b border-green-800 p-4">
                <div className="flex items-center gap-2 text-green-400">
                  <CheckCircle className="w-5 h-5" />
                  <span className="font-medium">
                    {connectedBrokers.length} broker{connectedBrokers.length > 1 ? 's' : ''} connected
                  </span>
                </div>
              </div>
            )}

            {/* Brokers Grid */}
            <div className="flex-1 overflow-y-auto p-6">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {brokers.map((broker) => (
                  <BrokerCard key={broker.id} broker={broker} />
                ))}
              </div>

              {/* Info Section */}
              <div className="mt-6 p-4 bg-[#1a1a1b] border border-gray-800 rounded-lg">
                <h3 className="text-white font-semibold mb-2">How it works:</h3>
                <ol className="text-gray-400 text-sm space-y-1 list-decimal list-inside">
                  <li>Click "Connect Account" on your preferred broker</li>
                  <li>You'll be redirected to securely login to your broker account</li>
                  <li>Authorize RISEDUALAI to access trading capabilities</li>
                  <li>Start executing trades directly from our platform</li>
                </ol>
                <p className="text-gray-500 text-xs mt-3">
                  Note: This feature requires broker API access. Some brokers may require additional approval.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default BrokerConnect;