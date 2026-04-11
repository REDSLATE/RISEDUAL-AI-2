import React, { useState, useEffect, useCallback } from 'react';
import ReactDOM from 'react-dom';
import {
  Building2, CheckCircle, X, ArrowRight, Shield, Eye, EyeOff,
  RefreshCw, TrendingUp, TrendingDown, DollarSign, BarChart3,
  Wallet, FileText, Trash2, Loader2, ExternalLink, AlertCircle,
  ArrowUpDown, Clock, Briefcase, Link2
} from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { authFetch } from '../contexts/AuthContext';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const BROKERS = [
  {
    id: 'alpaca',
    name: 'Alpaca',
    description: 'Commission-free API-first trading for stocks & crypto',
    features: ['Paper Trading', 'API Trading', 'No Minimums', 'Stocks & Crypto'],
    keyLabel: 'API Key ID',
    secretLabel: 'API Secret Key',
    hasPaper: true,
    docsUrl: 'https://alpaca.markets/docs/trading',
    signupUrl: 'https://app.alpaca.markets/signup',
    color: '#F7D046',
    recommended: true,
  },
  {
    id: 'schwab',
    name: 'Charles Schwab',
    description: 'Full-service brokerage with professional tools',
    features: ['Stocks & ETFs', 'Options', 'Research Tools', 'Banking'],
    keyLabel: 'Access Token',
    secretLabel: 'App Secret',
    hasPaper: false,
    docsUrl: 'https://developer.schwab.com',
    signupUrl: 'https://developer.schwab.com',
    color: '#00A0DF',
    recommended: false,
  },
  {
    id: 'ibkr',
    name: 'Interactive Brokers',
    description: 'Professional trading with global market access',
    features: ['Global Markets', 'Low Commissions', 'Professional API', 'Options'],
    keyLabel: 'Gateway URL',
    secretLabel: 'Account ID',
    hasPaper: false,
    docsUrl: 'https://www.interactivebrokers.com/api',
    signupUrl: 'https://www.interactivebrokers.com',
    color: '#D41F2C',
    recommended: false,
  },
  {
    id: 'moomoo',
    name: 'MooMoo',
    description: 'Commission-free trading with pro-level tools & data',
    features: ['Free Level 2 Data', 'Paper Trading', 'Extended Hours', 'Options'],
    keyLabel: 'App Token',
    secretLabel: 'Account ID',
    hasPaper: true,
    docsUrl: 'https://openapi.moomoo.com/docs',
    signupUrl: 'https://www.moomoo.com/us/openapi',
    color: '#FF6600',
    recommended: false,
  },
  {
    id: 'webull',
    name: 'Webull',
    description: 'Zero-commission trading with advanced charting',
    features: ['Zero Commission', 'Paper Trading', 'Extended Hours', 'Crypto'],
    keyLabel: 'Access Token',
    secretLabel: 'Device ID',
    hasPaper: true,
    docsUrl: 'https://www.webull.com/trading-api',
    signupUrl: 'https://www.webull.com',
    color: '#E02020',
    recommended: false,
  },
  {
    id: 'robinhood',
    name: 'Robinhood',
    description: 'Simple, commission-free investing for everyone',
    features: ['Commission-Free', 'Fractional Shares', 'Crypto', 'Cash Management'],
    keyLabel: 'OAuth Token',
    secretLabel: 'Account URL (optional)',
    hasPaper: false,
    docsUrl: 'https://robinhood.com/us/en/about/api',
    signupUrl: 'https://robinhood.com',
    color: '#00C805',
    recommended: false,
  },
  {
    id: 'public',
    name: 'Public.com',
    description: 'Commission-free stocks, ETFs, options, crypto & bonds with social features',
    features: ['Commission-Free', 'Options & Crypto', 'Bond Trading', 'Social Feed'],
    keyLabel: 'API Token',
    secretLabel: 'Account ID',
    hasPaper: false,
    docsUrl: 'https://public.com/api/docs',
    signupUrl: 'https://public.com/api',
    color: '#000000',
    recommended: false,
  },
  {
    id: 'kraken',
    name: 'Kraken',
    description: 'Top-tier crypto exchange with advanced trading & staking',
    features: ['Crypto Exchange', 'Margin Trading', 'Staking', 'Futures'],
    keyLabel: 'API Key',
    secretLabel: 'Private Key (Base64)',
    hasPaper: false,
    docsUrl: 'https://docs.kraken.com/api',
    signupUrl: 'https://www.kraken.com/features/trading-api',
    color: '#7B61FF',
    recommended: false,
  },
];

// ─── Helpers ───

const ORDER_STATUS_CLASSES = {
  filled: 'bg-lime-600 text-lime-400',
  cancelled: 'bg-slate-700 text-slate-400',
};
const getOrderStatusClass = (status) => ORDER_STATUS_CLASSES[status] || 'bg-amber-900/50 text-amber-300';

// ─── Sub-components ───

const ConnectForm = ({ broker, onConnect, onCancel }) => {
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [paper, setPaper] = useState(true);
  const [showSecret, setShowSecret] = useState(false);
  const [loading, setLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState(false);
  const [oauthAvailable, setOauthAvailable] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    // Check OAuth availability for this broker
    (async () => {
      try {
        const res = await authFetch(`${API}/broker/oauth/${broker.id}/status`);
        if (res.ok) {
          const data = await res.json();
          setOauthAvailable(data.available);
        }
      } catch { /* OAuth not available */ }
    })();
  }, [broker.id]);

  const handleOAuth = async () => {
    setOauthLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API}/broker/oauth/${broker.id}/authorize`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `OAuth init failed (${res.status})`);
      }
      const data = await res.json();
      // Open broker authorization in a new window
      window.location.href = data.authorize_url;
    } catch (err) {
      setError(err.message);
      setOauthLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) { setError('Both fields are required'); return; }
    setError('');
    setLoading(true);
    try {
      const res = await authFetch(`${API}/broker/connect`, {
        method: 'POST',
        body: JSON.stringify({ broker_id: broker.id, api_key: apiKey.trim(), api_secret: apiSecret.trim(), paper }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Connection failed (${res.status})`);
      }
      const data = await res.json();
      onConnect(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-slate-800/60 border border-slate-600/50 rounded-xl p-5 mt-3">
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-white font-semibold text-sm flex items-center gap-2">
          <Link2 className="w-4 h-4 text-[#3DE8D9]" />
          Connect {broker.name}
        </h4>
        <button onClick={onCancel} className="text-slate-400 hover:text-white"><X className="w-4 h-4" /></button>
      </div>

      {error && (
        <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-xs p-2.5 rounded-lg mb-3 flex items-start gap-2" data-testid="broker-connect-error">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" /><span>{error}</span>
        </div>
      )}

      {/* OAuth Connect Button */}
      {oauthAvailable && (
        <div className="mb-4">
          <Button onClick={handleOAuth} disabled={oauthLoading}
            className="w-full bg-gradient-to-r from-[#3DE8D9] to-[#7AEEE0] hover:from-[#7AEEE0] hover:to-[#3B82F6] text-white text-sm rounded-lg py-3 font-semibold"
            data-testid={`broker-oauth-btn-${broker.id}`}>
            {oauthLoading ? (
              <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Redirecting to {broker.name}...</>
            ) : (
              <><ExternalLink className="w-4 h-4 mr-2" />Connect with {broker.name} (OAuth)</>
            )}
          </Button>
          <div className="flex items-center gap-3 my-3">
            <div className="flex-1 h-px bg-slate-700" />
            <span className="text-slate-300 text-xs">or enter API keys manually</span>
            <div className="flex-1 h-px bg-slate-700" />
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label className="text-slate-300 text-xs mb-1 block">{broker.keyLabel}</label>
          <Input
            value={apiKey} onChange={e => setApiKey(e.target.value)}
            placeholder={`Enter your ${broker.keyLabel}`}
            className="bg-slate-900 border-slate-600 text-white text-sm rounded-lg"
            data-testid={`broker-key-input-${broker.id}`}
          />
        </div>
        <div className="relative">
          <label className="text-slate-300 text-xs mb-1 block">{broker.secretLabel}</label>
          <Input
            type={showSecret ? 'text' : 'password'}
            value={apiSecret} onChange={e => setApiSecret(e.target.value)}
            placeholder={`Enter your ${broker.secretLabel}`}
            className="bg-slate-900 border-slate-600 text-white text-sm rounded-lg pr-10"
            data-testid={`broker-secret-input-${broker.id}`}
          />
          <button type="button" onClick={() => setShowSecret(!showSecret)}
            className="absolute right-3 top-[calc(50%+6px)] text-slate-400">
            {showSecret ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          </button>
        </div>

        {broker.hasPaper && (
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={paper} onChange={e => setPaper(e.target.checked)}
              className="rounded bg-slate-700 border-slate-500 text-[#3DE8D9] focus:ring-[#3DE8D9]"
              data-testid={`broker-paper-toggle-${broker.id}`}
            />
            <span className="text-slate-300 text-sm">Paper Trading (simulated)</span>
          </label>
        )}

        <div className="flex gap-2 pt-1">
          <Button type="submit" disabled={loading}
            className="flex-1 bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white text-sm rounded-lg"
            data-testid={`broker-connect-submit-${broker.id}`}>
            {loading ? <><Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />Connecting...</> : <>Connect<ArrowRight className="w-3.5 h-3.5 ml-1.5" /></>}
          </Button>
          <a href={broker.signupUrl} target="_blank" rel="noreferrer"
            className="flex items-center gap-1 text-slate-400 hover:text-white text-xs px-3 py-2 bg-slate-700/50 rounded-lg transition-colors">
            Get Keys<ExternalLink className="w-3 h-3" />
          </a>
        </div>
      </form>
    </div>
  );
};


const AccountDashboard = ({ brokerId, onDisconnect, onSync }) => {
  const [account, setAccount] = useState(null);
  const [positions, setPositions] = useState([]);
  const [orders, setOrders] = useState([]);
  const [tab, setTab] = useState('positions');
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [orderForm, setOrderForm] = useState(null);
  const [error, setError] = useState('');

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [accRes, posRes, ordRes] = await Promise.all([
        authFetch(`${API}/broker/account/${brokerId}`),
        authFetch(`${API}/broker/positions/${brokerId}`),
        authFetch(`${API}/broker/orders/${brokerId}?status=all`),
      ]);
      if (accRes.ok) setAccount(await accRes.json());
      if (posRes.ok) { const d = await posRes.json(); setPositions(d.positions || []); }
      if (ordRes.ok) { const d = await ordRes.json(); setOrders(d.orders || []); }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [brokerId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await authFetch(`${API}/broker/portfolio-sync/${brokerId}`);
      if (res.ok) {
        const data = await res.json();
        onSync?.(data);
      }
    } catch (err) {
      logger.error('Portfolio sync failed:', err);
      setError(err.message || 'Portfolio sync failed');
      setTimeout(() => setError(''), 4000);
    } finally {
      setSyncing(false);
    }
  };

  const handlePlaceOrder = async (e) => {
    e.preventDefault();
    if (!orderForm) return;
    try {
      const res = await authFetch(`${API}/broker/order/${brokerId}`, {
        method: 'POST',
        body: JSON.stringify(orderForm),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Order failed');
      setOrderForm(null);
      fetchData();
    } catch (err) {
      setError(err.message);
      setTimeout(() => setError(''), 4000);
    }
  };

  const handleCancelOrder = async (orderId) => {
    try {
      await authFetch(`${API}/broker/order/${brokerId}/${orderId}`, { method: 'DELETE' });
      fetchData();
    } catch (err) {
      logger.error('Cancel order failed:', err);
      setError(err.message || 'Failed to cancel order');
      setTimeout(() => setError(''), 4000);
    }
  };

  const fmt = (v) => {
    const n = parseFloat(v);
    return isNaN(n) ? '$0.00' : `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 text-[#3DE8D9] animate-spin" />
        <span className="text-slate-400 ml-2 text-sm">Loading account...</span>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid={`broker-dashboard-${brokerId}`}>
      {error && (
        <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-xs p-2.5 rounded-lg flex items-start gap-2">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" /><span>{error}</span>
        </div>
      )}

      {/* Account Summary */}
      {account && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'Portfolio', value: fmt(account.portfolio_value), icon: Briefcase, color: 'text-white' },
            { label: 'Cash', value: fmt(account.cash), icon: DollarSign, color: 'text-lime-400' },
            { label: 'Buying Power', value: fmt(account.buying_power), icon: Wallet, color: 'text-[#3DE8D9]' },
            { label: 'Equity', value: fmt(account.equity), icon: BarChart3, color: 'text-amber-300' },
          ].map(({ label, value, icon: Icon, color }) => (
            <div key={label} className="bg-slate-800/60 border border-slate-400/30/30 rounded-xl p-3">
              <div className="flex items-center gap-1.5 mb-1">
                <Icon className={`w-3.5 h-3.5 ${color}`} />
                <span className="text-slate-300 text-xs">{label}</span>
              </div>
              <p className={`text-base font-bold ${color}`} data-testid={`account-${label.toLowerCase().replace(' ', '-')}`}>{value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Action bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button size="sm" onClick={fetchData} className="bg-slate-700 hover:bg-slate-600 text-white text-xs rounded-lg" data-testid="broker-refresh-btn">
          <RefreshCw className="w-3 h-3 mr-1" />Refresh
        </Button>
        <Button size="sm" onClick={handleSync} disabled={syncing} className="bg-[#3DE8D9]/20 hover:bg-[#3DE8D9]/30 text-[#3DE8D9] text-xs rounded-lg border border-[#3DE8D9]/30" data-testid="broker-sync-btn">
          {syncing ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <RefreshCw className="w-3 h-3 mr-1" />}Sync Portfolio
        </Button>
        <Button size="sm" onClick={() => setOrderForm({ symbol: '', quantity: 1, side: 'buy', order_type: 'market', time_in_force: 'day' })}
          className="bg-green-600/20 hover:bg-green-600/30 text-lime-400 text-xs rounded-lg border border-emerald-600/30" data-testid="broker-new-order-btn">
          <ArrowUpDown className="w-3 h-3 mr-1" />New Order
        </Button>
        <Button size="sm" onClick={onDisconnect} className="bg-orange-900 hover:bg-orange-800 text-orange-400 text-xs rounded-lg border border-orange-700/30 ml-auto" data-testid="broker-disconnect-btn">
          <Trash2 className="w-3 h-3 mr-1" />Disconnect
        </Button>
      </div>

      {/* Order Form */}
      {orderForm && (
        <form onSubmit={handlePlaceOrder} className="bg-slate-800/60 border border-slate-600/50 rounded-xl p-4 space-y-3" data-testid="order-form">
          <div className="flex items-center justify-between">
            <h4 className="text-white font-semibold text-sm">Place Order</h4>
            <button type="button" onClick={() => setOrderForm(null)} className="text-slate-400 hover:text-white"><X className="w-4 h-4" /></button>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Input placeholder="Symbol (e.g. AAPL)" value={orderForm.symbol} data-testid="order-symbol-input"
              onChange={e => setOrderForm(p => ({ ...p, symbol: e.target.value.toUpperCase() }))}
              className="bg-slate-900 border-slate-600 text-white text-sm rounded-lg" />
            <Input type="number" min="1" placeholder="Qty" value={orderForm.quantity} data-testid="order-qty-input"
              onChange={e => setOrderForm(p => ({ ...p, quantity: parseFloat(e.target.value) || 1 }))}
              className="bg-slate-900 border-slate-600 text-white text-sm rounded-lg" />
            <select value={orderForm.side} onChange={e => setOrderForm(p => ({ ...p, side: e.target.value }))}
              className="bg-slate-900 border border-slate-600 text-white text-sm rounded-lg px-3 py-2" data-testid="order-side-select">
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
            <select value={orderForm.order_type} onChange={e => setOrderForm(p => ({ ...p, order_type: e.target.value }))}
              className="bg-slate-900 border border-slate-600 text-white text-sm rounded-lg px-3 py-2" data-testid="order-type-select">
              <option value="market">Market</option>
              <option value="limit">Limit</option>
              <option value="stop">Stop</option>
            </select>
          </div>
          {(orderForm.order_type === 'limit' || orderForm.order_type === 'stop_limit') && (
            <Input type="number" step="0.01" placeholder="Limit Price"
              onChange={e => setOrderForm(p => ({ ...p, limit_price: parseFloat(e.target.value) || null }))}
              className="bg-slate-900 border-slate-600 text-white text-sm rounded-lg" />
          )}
          <Button type="submit" className={`w-full text-sm rounded-lg ${orderForm.side === 'buy' ? 'bg-green-600 hover:bg-green-500' : 'bg-red-600 hover:bg-red-500'} text-white`}
            data-testid="order-submit-btn">
            {orderForm.side === 'buy' ? 'Buy' : 'Sell'} {orderForm.symbol || '...'}
          </Button>
        </form>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-slate-700/40 rounded-lg p-0.5">
        {[
          { id: 'positions', label: 'Positions', icon: TrendingUp, count: positions.length },
          { id: 'orders', label: 'Orders', icon: FileText, count: orders.length },
        ].map(({ id, label, icon: Icon, count }) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-xs font-medium rounded-md transition-all ${tab === id ? 'bg-[#3DE8D9] text-white' : 'text-slate-400 hover:text-white'}`}
            data-testid={`broker-tab-${id}`}>
            <Icon className="w-3.5 h-3.5" />{label}
            {count > 0 && <Badge className="bg-slate-600/50 text-[10px] px-1.5">{count}</Badge>}
          </button>
        ))}
      </div>

      {/* Positions */}
      {tab === 'positions' && (
        <div className="space-y-2">
          {positions.length === 0 ? (
            <p className="text-slate-300 text-sm text-center py-6">No open positions</p>
          ) : positions.map((p, i) => (
            <div key={`${p.symbol}-${i}`} className="bg-slate-700/40 border border-slate-400/30/30 rounded-lg p-3 flex items-center justify-between">
              <div>
                <span className="text-white font-semibold text-sm">{p.symbol}</span>
                <span className="text-slate-300 text-xs ml-2">{p.qty} shares</span>
              </div>
              <div className="text-right">
                <p className="text-white text-sm font-medium">{fmt(p.market_value)}</p>
                <p className={`text-xs font-medium ${p.unrealized_pl >= 0 ? 'text-lime-400' : 'text-orange-400'}`}>
                  {p.unrealized_pl >= 0 ? '+' : ''}{fmt(p.unrealized_pl)} ({(p.unrealized_plpc * 100).toFixed(2)}%)
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Orders */}
      {tab === 'orders' && (
        <div className="space-y-2">
          {orders.length === 0 ? (
            <p className="text-slate-300 text-sm text-center py-6">No orders</p>
          ) : orders.slice(0, 20).map((o, i) => (
            <div key={o.id || i} className="bg-slate-700/40 border border-slate-400/30/30 rounded-lg p-3 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Badge className={`text-[10px] ${o.side === 'buy' ? 'bg-lime-600 text-lime-400' : 'bg-orange-700 text-orange-400'}`}>
                  {o.side?.toUpperCase()}
                </Badge>
                <div>
                  <span className="text-white font-semibold text-sm">{o.symbol}</span>
                  <span className="text-slate-300 text-xs ml-2">{o.qty} @ {o.type}</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge className={`text-[10px] ${getOrderStatusClass(o.status)}`}>
                  {o.status}
                </Badge>
                {(o.status === 'new' || o.status === 'accepted' || o.status === 'pending_new') && (
                  <button onClick={() => handleCancelOrder(o.id)} className="text-orange-400 hover:text-orange-300 text-xs" data-testid={`cancel-order-${o.id}`}>
                    Cancel
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};


// ─── Main Component ───

const BrokerConnect = () => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [connections, setConnections] = useState([]);
  const [connectingBroker, setConnectingBroker] = useState(null);
  const [activeBroker, setActiveBroker] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [oauthMessage, setOauthMessage] = useState(null);

  // Handle OAuth redirect callback
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connectedBroker = params.get('broker_connected');
    const brokerError = params.get('broker_error');
    if (connectedBroker) {
      setOauthMessage({ type: 'success', text: `Successfully connected ${connectedBroker} via OAuth!` });
      setIsModalOpen(true);
      setActiveBroker(connectedBroker);
      // Clean URL
      window.history.replaceState({}, '', window.location.pathname);
      setTimeout(() => setOauthMessage(null), 5000);
    } else if (brokerError) {
      setOauthMessage({ type: 'error', text: `Broker connection failed: ${brokerError}` });
      setIsModalOpen(true);
      window.history.replaceState({}, '', window.location.pathname);
      setTimeout(() => setOauthMessage(null), 8000);
    }
  }, []);

  const fetchConnections = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/broker/connections`);
      if (res.ok) {
        const data = await res.json();
        setConnections(data.connections || []);
      }
    } catch (err) {
      logger.error('Fetch connections failed:', err);
    }
  }, []);

  useEffect(() => {
    if (isModalOpen) { fetchConnections(); }
  }, [isModalOpen, fetchConnections]);

  const handleConnected = (data) => {
    setConnectingBroker(null);
    fetchConnections();
    setActiveBroker(data.broker_id);
  };

  const handleDisconnect = async (brokerId) => {
    setLoading(true);
    try {
      await authFetch(`${API}/broker/disconnect/${brokerId}`, { method: 'DELETE' });
      setActiveBroker(null);
      fetchConnections();
    } catch (err) {
      logger.error('Broker disconnect failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const connectedIds = connections.map(c => c.broker_id);

  return (
    <>
      <Button onClick={() => setIsModalOpen(true)}
        className="bg-[#3DE8D9] hover:bg-[#7AEEE0]" data-testid="broker-connect-trigger">
        <Building2 className="w-4 h-4 mr-2" />Connect Broker
        {connectedIds.length > 0 && <Badge className="ml-2 bg-green-600">{connectedIds.length}</Badge>}
      </Button>

      {isModalOpen && ReactDOM.createPortal(
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="broker-modal">
          <div className="bg-slate-900 rounded-2xl max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col border border-slate-400/25">
            {/* Header */}
            <div className="border-b border-slate-400/30 p-5 flex items-start justify-between flex-shrink-0">
              <div>
                <h2 className="text-white text-xl font-bold mb-1">Connect Your Broker</h2>
                <p className="text-slate-300 text-sm">Link your brokerage to trade directly from RISEDUAL AI</p>
                <div className="flex items-center gap-2 mt-2 text-xs text-slate-400">
                  <Shield className="w-3.5 h-3.5" />
                  <span>Encrypted storage &middot; Keys never leave our server &middot; Disconnect anytime</span>
                </div>
              </div>
              <button onClick={() => setIsModalOpen(false)} className="text-slate-400 hover:text-white p-1" data-testid="broker-modal-close">
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Connected Summary */}
            {oauthMessage && (
              <div className={`px-5 py-3 flex items-center gap-2 text-sm border-b ${oauthMessage.type === 'success' ? 'bg-green-600 border-lime-700/30 text-lime-400' : 'bg-orange-900 border-orange-700/30 text-orange-400'}`}>
                {oauthMessage.type === 'success' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                <span>{oauthMessage.text}</span>
              </div>
            )}
            {connections.length > 0 && (
              <div className="bg-emerald-900/15 border-b border-lime-700/30 px-5 py-3 flex items-center justify-between flex-shrink-0">
                <div className="flex items-center gap-2 text-lime-400 text-sm">
                  <CheckCircle className="w-4 h-4" />
                  <span className="font-medium">{connections.length} broker{connections.length > 1 ? 's' : ''} connected</span>
                </div>
                {syncResult && (
                  <span className="text-lime-300/70 text-xs">{syncResult.symbols_synced?.length || 0} symbols synced to watchlist</span>
                )}
              </div>
            )}

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {/* If a broker dashboard is active */}
              {activeBroker && connectedIds.includes(activeBroker) ? (
                <div>
                  <button onClick={() => setActiveBroker(null)} className="text-slate-400 hover:text-white text-xs mb-3 flex items-center gap-1">
                    &larr; Back to all brokers
                  </button>
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center text-lg font-bold" style={{ backgroundColor: BROKERS.find(b => b.id === activeBroker)?.color + '20', color: BROKERS.find(b => b.id === activeBroker)?.color }}>
                      {BROKERS.find(b => b.id === activeBroker)?.name[0]}
                    </div>
                    <h3 className="text-white font-bold text-lg">{BROKERS.find(b => b.id === activeBroker)?.name}</h3>
                    {connections.find(c => c.broker_id === activeBroker)?.paper && (
                      <Badge className="bg-amber-900/40 text-amber-300 text-[10px]">Paper Trading</Badge>
                    )}
                  </div>
                  <AccountDashboard
                    brokerId={activeBroker}
                    onDisconnect={() => handleDisconnect(activeBroker)}
                    onSync={setSyncResult}
                  />
                </div>
              ) : (
                /* Broker Cards Grid */
                <div className="space-y-3">
                  {BROKERS.map(broker => {
                    const isConnected = connectedIds.includes(broker.id);
                    const isConnecting = connectingBroker === broker.id;
                    return (
                      <div key={broker.id} className={`bg-slate-700/40 border rounded-xl p-4 transition-all ${isConnected ? 'border-emerald-700/40' : 'border-slate-400/30/30 hover:border-slate-600'}`}
                        data-testid={`broker-card-${broker.id}`}>
                        <div className="flex items-start justify-between">
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-xl flex items-center justify-center text-lg font-bold" style={{ backgroundColor: broker.color + '15', color: broker.color }}>
                              {broker.name[0]}
                            </div>
                            <div>
                              <div className="flex items-center gap-2">
                                <h4 className="text-white font-semibold text-sm">{broker.name}</h4>
                                {broker.recommended && <Badge className="bg-[#3DE8D9]/20 text-[#3DE8D9] text-[10px]">Recommended</Badge>}
                                {isConnected && <Badge className="bg-lime-600 text-lime-400 text-[10px] flex items-center gap-0.5"><CheckCircle className="w-2.5 h-2.5" />{connections.find(c => c.broker_id === broker.id)?.auth_method === 'oauth' ? 'OAuth' : 'Connected'}</Badge>}
                              </div>
                              <p className="text-slate-300 text-xs mt-0.5">{broker.description}</p>
                            </div>
                          </div>
                          <div className="flex gap-2">
                            {isConnected ? (
                              <Button size="sm" onClick={() => setActiveBroker(broker.id)}
                                className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white text-xs rounded-lg"
                                data-testid={`broker-open-${broker.id}`}>
                                <BarChart3 className="w-3 h-3 mr-1" />Dashboard
                              </Button>
                            ) : (
                              <Button size="sm" onClick={() => setConnectingBroker(isConnecting ? null : broker.id)}
                                className="bg-slate-700 hover:bg-slate-600 text-white text-xs rounded-lg"
                                data-testid={`broker-connect-btn-${broker.id}`}>
                                {isConnecting ? 'Cancel' : <><ArrowRight className="w-3 h-3 mr-1" />Connect</>}
                              </Button>
                            )}
                          </div>
                        </div>

                        {/* Feature tags */}
                        <div className="flex flex-wrap gap-1.5 mt-3">
                          {broker.features.map(f => (
                            <span key={f} className="bg-slate-900/60 text-slate-400 text-[10px] px-2 py-0.5 rounded">{f}</span>
                          ))}
                        </div>

                        {/* Connect Form */}
                        {isConnecting && !isConnected && (
                          <ConnectForm broker={broker} onConnect={handleConnected} onCancel={() => setConnectingBroker(null)} />
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* How it works */}
              {!activeBroker && (
                <div className="p-4 bg-slate-700/35 border border-slate-400/30/30 rounded-xl">
                  <h3 className="text-white font-semibold text-sm mb-2">How it works</h3>
                  <ol className="text-slate-300 text-xs space-y-1 list-decimal list-inside">
                    <li>Sign up with your broker and get your API keys from their developer portal</li>
                    <li>Enter your API credentials above — they are encrypted at rest</li>
                    <li>View your account, manage positions, and execute trades from RISEDUAL AI</li>
                    <li>Sync your portfolio to power AI strategy recommendations</li>
                  </ol>
                </div>
              )}
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
};

export default BrokerConnect;
