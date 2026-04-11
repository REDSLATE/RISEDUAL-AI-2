import React, { useState, useEffect, useCallback } from 'react';
import { Radio, Zap, RefreshCw, Lock, AlertTriangle, TrendingUp, Star } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const severityStyle = (s) => ({
  high: { icon: 'text-red-400', bg: 'border-red-700/40 bg-red-900/10' },
  medium: { icon: 'text-amber-400', bg: 'border-amber-700/40 bg-amber-900/10' },
  low: { icon: 'text-blue-400', bg: 'border-blue-700/40 bg-blue-900/10' },
}[s] || { icon: 'text-slate-400', bg: 'border-slate-500/40/40' });

const MarketSignals = ({ onClose, onSubscribe }) => {
  const { isPro } = useAuth();
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);

  const fetchSignals = useCallback(async () => {
    if (!isPro) return;
    setLoading(true);
    try {
      const res = await authFetch(`${API}/signals`);
      if (res.ok) {
        const data = await res.json();
        setSignals(data.signals || []);
      }
    } catch (e) {
      logger.error('Signals fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, [isPro]);

  useEffect(() => { fetchSignals(); }, [fetchSignals]);

  const scan = async () => {
    setScanning(true);
    try {
      const res = await authFetch(`${API}/signals/scan`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (data.signals?.length) {
          setSignals(prev => [...data.signals, ...prev]);
        }
      }
    } catch (e) {
      logger.error('Scan error:', e);
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="market-signals">
      <div className="bg-slate-900 rounded-2xl max-w-lg w-full my-4 border border-slate-500/30">
        <div className="p-5 border-b border-slate-500/40 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-amber-500 to-red-500 rounded-xl flex items-center justify-center">
              <Radio className="w-5 h-5 text-white" />
            </div>
            <div>
              <h2 className="text-white text-lg font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>Market Signals</h2>
              <p className="text-slate-400 text-xs">AI-detected unusual activity</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge className="bg-gradient-to-r from-amber-500 to-red-500 text-white border-0 text-xs">PRO</Badge>
            <button onClick={onClose} className="text-slate-400 hover:text-white text-xl px-2">×</button>
          </div>
        </div>

        <div className="p-5">
          {!isPro ? (
            <div className="text-center py-8">
              <Lock className="w-10 h-10 text-slate-400 mx-auto mb-3" />
              <p className="text-white font-semibold mb-1">Pro Feature</p>
              <p className="text-slate-400 text-xs mb-4">AI monitors your watchlist for dark pool spikes, whale movements, and unusual options flow.</p>
              <Button className="bg-[#35D6C8] text-white rounded-xl" onClick={onSubscribe}>Upgrade to Pro</Button>
            </div>
          ) : (
            <div className="space-y-4">
              <Button onClick={scan} disabled={scanning} className="bg-[#35D6C8] text-white rounded-xl w-full">
                {scanning ? <><RefreshCw className="w-4 h-4 mr-2 animate-spin" /> Scanning Watchlist...</> : <><Zap className="w-4 h-4 mr-2" /> Scan Watchlist for Signals</>}
              </Button>

              {loading ? (
                <div className="flex items-center justify-center py-8"><RefreshCw className="w-6 h-6 text-[#35D6C8] animate-spin" /></div>
              ) : signals.length === 0 ? (
                <div className="text-center py-8">
                  <Radio className="w-10 h-10 text-slate-400 mx-auto mb-3" />
                  <p className="text-slate-400 text-sm">No signals detected yet</p>
                  <p className="text-slate-400 text-xs mt-1">Add tickers to your watchlist and scan for signals</p>
                </div>
              ) : (
                <div className="space-y-2 max-h-[400px] overflow-y-auto">
                  {signals.map((sig, i) => {
                    const style = severityStyle(sig.severity);
                    return (
                      <Card key={`sig-${i}-${sig.ticker}`} className={`border rounded-xl p-3 ${style.bg}`} data-testid={`signal-${i}`}>
                        <div className="flex items-start gap-3">
                          <div className="mt-0.5">
                            {sig.type === 'dark_pool_spike' ? <AlertTriangle className={`w-4 h-4 ${style.icon}`} /> : <TrendingUp className={`w-4 h-4 ${style.icon}`} />}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-0.5">
                              <span className="text-white text-sm font-semibold">{sig.ticker}</span>
                              <Badge className={`text-[10px] border-0 ${sig.severity === 'high' ? 'bg-red-900/40 text-red-400' : 'bg-amber-900/40 text-amber-400'}`}>{sig.severity?.toUpperCase()}</Badge>
                            </div>
                            <p className="text-white text-xs font-medium">{sig.title}</p>
                            <p className="text-slate-400 text-[11px] mt-0.5">{sig.detail}</p>
                            <p className="text-slate-400 text-[10px] mt-1">
                              {sig.detected_at ? new Date(sig.detected_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''}
                            </p>
                          </div>
                        </div>
                      </Card>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default MarketSignals;
