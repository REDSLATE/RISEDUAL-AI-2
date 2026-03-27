import React, { useState, useEffect } from 'react';
import { Bell, X, CheckCircle, AlertCircle, TrendingUp } from 'lucide-react';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';

const AlertsPanel = () => {
  const [alerts, setAlerts] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    // Simulate real-time alerts
    const interval = setInterval(() => {
      generateAlert();
    }, 30000); // Every 30 seconds

    return () => clearInterval(interval);
  }, []);

  const generateAlert = () => {
    const alertTypes = [
      { type: 'unusual_volume', icon: TrendingUp, color: 'text-orange-400' },
      { type: 'price_alert', icon: AlertCircle, color: 'text-yellow-400' },
      { type: 'dark_pool', icon: AlertCircle, color: 'text-purple-400' },
    ];

    const symbols = ['AAPL', 'TSLA', 'NVDA', 'META', 'GOOGL'];
    const alertType = alertTypes[Math.floor(Math.random() * alertTypes.length)];
    const symbol = symbols[Math.floor(Math.random() * symbols.length)];

    const newAlert = {
      id: Date.now(),
      symbol,
      type: alertType.type,
      icon: alertType.icon,
      color: alertType.color,
      message: getAlertMessage(alertType.type, symbol),
      timestamp: new Date(),
      read: false
    };

    setAlerts(prev => [newAlert, ...prev].slice(0, 20));
    setUnreadCount(prev => prev + 1);
  };

  const getAlertMessage = (type, symbol) => {
    switch (type) {
      case 'unusual_volume':
        return `${symbol} - Unusual options volume detected`;
      case 'price_alert':
        return `${symbol} - Price movement above threshold`;
      case 'dark_pool':
        return `${symbol} - Significant dark pool activity`;
      default:
        return `${symbol} - Alert triggered`;
    }
  };

  const markAsRead = (id) => {
    setAlerts(prev => prev.map(alert => 
      alert.id === id ? { ...alert, read: true } : alert
    ));
    setUnreadCount(prev => Math.max(0, prev - 1));
  };

  const clearAll = () => {
    setAlerts([]);
    setUnreadCount(0);
  };

  return (
    <div className="fixed top-20 right-6 z-40">
      {/* Bell Icon */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="relative bg-[#0F172A] border border-slate-700 rounded-full p-3 hover:bg-[#0F172A] transition-colors"
      >
        <Bell className="w-5 h-5 text-white" />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 bg-red-500 text-white text-xs rounded-full w-5 h-5 flex items-center justify-center">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {/* Alerts Panel */}
      {isOpen && (
        <Card className="absolute top-12 right-0 w-96 bg-slate-900 border-slate-700/50 shadow-2xl rounded-xl max-h-[500px] overflow-hidden flex flex-col">
          {/* Header */}
          <div className="border-b border-slate-700 p-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-white font-semibold">Alerts</h3>
              {unreadCount > 0 && (
                <Badge className="bg-red-500">{unreadCount} new</Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              {alerts.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={clearAll}
                  className="text-slate-400 hover:text-slate-50 text-xs"
                >
                  Clear all
                </Button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="text-slate-400 hover:text-slate-50"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Alerts List */}
          <div className="overflow-y-auto flex-1">
            {alerts.length === 0 ? (
              <div className="text-center py-8 px-4">
                <Bell className="w-12 h-12 text-gray-600 mx-auto mb-2" />
                <p className="text-slate-400">No alerts yet</p>
                <p className="text-gray-600 text-sm">You'll be notified of unusual activity</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-800">
                {alerts.map((alert) => (
                  <div
                    key={alert.id}
                    className={`p-4 hover:bg-[#1E293B] transition-colors cursor-pointer ${
                      !alert.read ? 'bg-slate-800' : ''
                    }`}
                    onClick={() => markAsRead(alert.id)}
                  >
                    <div className="flex items-start gap-3">
                      <alert.icon className={`w-5 h-5 ${alert.color} flex-shrink-0 mt-0.5`} />
                      <div className="flex-1 min-w-0">
                        <p className="text-white text-sm">{alert.message}</p>
                        <p className="text-slate-500 text-xs mt-1">
                          {alert.timestamp.toLocaleTimeString()}
                        </p>
                      </div>
                      {!alert.read && (
                        <div className="w-2 h-2 bg-blue-500 rounded-full flex-shrink-0 mt-2" />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
};

export default AlertsPanel;