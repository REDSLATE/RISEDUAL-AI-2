import React, { useState, useEffect, useCallback } from 'react';
import { Bot, Grid3X3, Radio, Webhook, Power, Plus, ChevronRight, RefreshCw } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import InfoTooltip from './InfoTooltip';

const API = `${getApiBase()}/api/bots`;
const BOT_ICONS = { grid: Grid3X3, signal: Radio, webhook: Webhook };
const BOT_COLORS = { grid: 'text-[#3DE8D9]', signal: 'text-violet-400', webhook: 'text-amber-400' };

const BotsDashboard = ({ onOpenBots }) => {
  const { user } = useAuth();
  const [bots, setBots] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadBots = useCallback(async () => {
    if (!user) return;
    try {
      const res = await authFetch(API);
      if (res.ok) setBots(await res.json());
    } catch { /* */ }
    finally { setLoading(false); }
  }, [user]);

  useEffect(() => { loadBots(); }, [loadBots]);

  const toggleBot = async (botId, enabled) => {
    try {
      const res = await authFetch(`${API}/${botId}/toggle`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (res.ok) { toast.success(enabled ? 'Bot activated' : 'Bot deactivated'); loadBots(); }
    } catch { toast.error('Toggle failed'); }
  };

  if (!user) return null;

  const activeCount = bots.filter(b => b.enabled).length;
  const totalTrades = bots.reduce((s, b) => s + (b.stats?.trades || 0), 0);

  return (
    <Card className="bg-[#0B1426] border-slate-600/30" data-testid="bots-dashboard">
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="w-5 h-5 text-[#3DE8D9]" />
          <CardTitle className="text-white text-base">Trading Bots</CardTitle>
          <InfoTooltip id="trading-bots" />
          <Badge className={`text-[9px] ${activeCount > 0 ? 'bg-lime-500/15 text-lime-400' : 'bg-slate-700 text-slate-400'}`}>
            {activeCount} active
          </Badge>
        </div>
        <Button size="sm" variant="outline" onClick={onOpenBots}
          className="bg-slate-800/60 border-slate-600/30 text-[#3DE8D9] text-xs h-7 hover:bg-slate-700" data-testid="bots-dashboard-manage">
          Manage <ChevronRight className="w-3 h-3 ml-1" />
        </Button>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex items-center justify-center py-6">
            <RefreshCw className="w-4 h-4 text-[#3DE8D9] animate-spin" />
          </div>
        ) : bots.length === 0 ? (
          <div className="text-center py-6">
            <p className="text-slate-400 text-xs mb-2">No bots created yet</p>
            <Button size="sm" onClick={onOpenBots} className="bg-[#3DE8D9] text-white text-xs h-7">
              <Plus className="w-3 h-3 mr-1" /> Create Bot
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            {bots.slice(0, 4).map((bot, i) => {
              const Icon = BOT_ICONS[bot.type] || Bot;
              const color = BOT_COLORS[bot.type] || 'text-slate-400';
              return (
                <div key={bot.bot_id || `db-${i}`} className="flex items-center justify-between py-2 px-3 bg-slate-800/30 rounded-lg border border-slate-600/15">
                  <div className="flex items-center gap-2 min-w-0">
                    <Icon className={`w-4 h-4 ${color} shrink-0`} />
                    <div className="min-w-0">
                      <p className="text-white text-xs font-medium truncate">{bot.name}</p>
                      <div className="flex items-center gap-2 text-[9px] text-slate-500">
                        <span>{bot.stats?.trades || 0} trades</span>
                        {bot.type === 'grid' && bot.config?.symbol && <span>{bot.config.symbol}</span>}
                        {bot.type === 'signal' && <span>Min {bot.config?.min_confidence || 70}%</span>}
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={() => toggleBot(bot.bot_id, !bot.enabled)}
                    className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${bot.enabled ? 'bg-lime-500' : 'bg-slate-700'}`}
                    data-testid={`bots-dash-toggle-${bot.bot_id}`}
                  >
                    <div className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform"
                      style={{ transform: bot.enabled ? 'translateX(16px)' : 'translateX(2px)' }} />
                  </button>
                </div>
              );
            })}
            {bots.length > 4 && (
              <button onClick={onOpenBots} className="text-[#3DE8D9] text-[10px] hover:text-white w-full text-center py-1">
                + {bots.length - 4} more bots
              </button>
            )}
            <div className="flex items-center justify-between pt-1 border-t border-slate-600/15 text-[9px] text-slate-500">
              <span>Total trades: {totalTrades}</span>
              <span>{activeCount}/{bots.length} running</span>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default BotsDashboard;
