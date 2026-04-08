import React, { useState, useEffect, useCallback } from 'react';
import { Bell, X, Lock } from 'lucide-react';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { useAuth, authFetch } from '../contexts/AuthContext';
import NotificationItem from './alerts/NotificationItem';
import logger from '../utils/logger';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AlertsPanel = ({ onSubscribe }) => {
  const { user, isPro } = useAuth();
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const fetchNotifications = useCallback(async () => {
    if (!user || !isPro) return;
    try {
      const res = await authFetch(`${API}/notifications`);
      if (res.ok) {
        const data = await res.json();
        setNotifications(data.notifications || []);
        setUnreadCount(data.unread_count || 0);
      }
    } catch (e) {
      logger.error('Notification fetch error:', e);
    }
  }, [user, isPro]);

  useEffect(() => {
    fetchNotifications();
    if (!isPro) return;
    const interval = setInterval(fetchNotifications, 60000);
    return () => clearInterval(interval);
  }, [fetchNotifications, isPro]);

  const markAllRead = async () => {
    setLoading(true);
    try {
      await authFetch(`${API}/notifications/read-all`, { method: 'POST' });
      setNotifications(prev => prev.map(n => ({ ...n, read: true })));
      setUnreadCount(0);
    } catch (e) {
      logger.error('Mark read error:', e);
    } finally {
      setLoading(false);
    }
  };

  if (!user) return null;

  return (
    <div className="fixed top-20 right-6 z-40" data-testid="alerts-panel">
      {/* Bell Icon */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="relative bg-[#0F172A] border border-slate-700 rounded-full p-3 hover:bg-slate-800 transition-colors"
        data-testid="alerts-bell"
      >
        <Bell className="w-5 h-5 text-white" />
        {isPro && unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 bg-[#0052FF] text-white text-xs rounded-full w-5 h-5 flex items-center justify-center font-bold animate-pulse">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
        {!isPro && (
          <span className="absolute -top-1 -right-1 bg-slate-600 text-slate-300 text-[8px] rounded-full w-4 h-4 flex items-center justify-center">
            <Lock className="w-2.5 h-2.5" />
          </span>
        )}
      </button>

      {/* Panel */}
      {isOpen && (
        <Card className="absolute top-14 right-0 w-[340px] sm:w-96 bg-slate-900 border-slate-700/50 shadow-2xl rounded-xl max-h-[450px] overflow-hidden flex flex-col" data-testid="alerts-dropdown">
          <div className="border-b border-slate-700 p-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-white font-semibold text-sm">AI Alerts</h3>
              {isPro && unreadCount > 0 && (
                <Badge className="bg-[#0052FF] text-white text-[10px] px-1.5 py-0">{unreadCount} new</Badge>
              )}
              {isPro && <Badge className="bg-[#0052FF]/20 text-[#0052FF] border-0 text-[10px]">PRO</Badge>}
            </div>
            <div className="flex items-center gap-1">
              {isPro && notifications.length > 0 && (
                <Button variant="ghost" size="sm" onClick={markAllRead} disabled={loading || unreadCount === 0}
                  className="text-slate-400 hover:text-white text-[10px] h-7 px-2">
                  Mark all read
                </Button>
              )}
              <button onClick={() => setIsOpen(false)} className="text-slate-400 hover:text-white p-1">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="overflow-y-auto flex-1">
            {!isPro ? (
              /* Free user — paywall teaser */
              <div className="text-center py-8 px-6" data-testid="alerts-paywall">
                <div className="w-14 h-14 bg-slate-800 rounded-2xl flex items-center justify-center mx-auto mb-4">
                  <Lock className="w-7 h-7 text-slate-500" />
                </div>
                <h4 className="text-white font-semibold mb-1">Pro AI Alerts</h4>
                <p className="text-slate-400 text-xs leading-relaxed mb-4">
                  Get notified when our AI detects a verdict change on your watchlist tickers. Never miss a BUY→SELL flip.
                </p>
                <Button className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl text-sm w-full" onClick={() => { onSubscribe?.(); setIsOpen(false); }} data-testid="alerts-upgrade-btn">
                  Upgrade to Pro
                </Button>
              </div>
            ) : notifications.length === 0 ? (
              /* Pro but no notifications */
              <div className="text-center py-8 px-4">
                <Bell className="w-10 h-10 text-slate-600 mx-auto mb-3" />
                <p className="text-slate-400 text-sm">No alerts yet</p>
                <p className="text-slate-500 text-xs mt-1">You'll be notified when AI detects a verdict change on your watchlist tickers</p>
              </div>
            ) : (
              /* Pro with notifications */
              <div className="divide-y divide-slate-800/60">
                {notifications.map((n, i) => (
                  <NotificationItem key={n._id || n.id || `notif-${i}`} notification={n} index={i} />
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
