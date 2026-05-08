import React from 'react';
import { Trash2 } from 'lucide-react';
import { Badge } from '../ui/badge';

const ModeTag = ({ mode }) => {
  const colors = { paper: 'bg-lime-500/15 text-lime-400', live: 'bg-red-500/15 text-red-400', simulate: 'bg-blue-500/15 text-blue-400' };
  return <Badge className={`text-[9px] ${colors[mode] || 'bg-slate-700 text-slate-400'}`}>{mode?.toUpperCase()}</Badge>;
};

const StatusTag = ({ status }) => {
  const colors = {
    pending: 'bg-amber-500/15 text-amber-400', filled: 'bg-lime-500/15 text-lime-400',
    partially_filled: 'bg-blue-500/15 text-blue-400', stopped: 'bg-red-500/15 text-red-400',
    cancelled: 'bg-slate-600 text-slate-400', completed: 'bg-[#3DE8D9]/15 text-[#3DE8D9]',
  };
  return <Badge className={`text-[9px] ${colors[status] || 'bg-slate-700 text-slate-400'}`}>{status?.replace('_', ' ').toUpperCase()}</Badge>;
};

const SmartOrderList = ({ orders, cancelOrder }) => (
  <div className="p-4 space-y-2" data-testid="smart-orders-list">
    {orders.length === 0 ? (
      <p className="text-slate-400 text-sm text-center py-8">No smart orders yet</p>
    ) : (
      orders.map((o, i) => (
        <div key={o.order_id || `order-${i}`} className="bg-slate-800/40 rounded-xl p-3 border border-slate-600/20">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-white font-bold text-sm">{o.symbol}</span>
              <Badge className={`text-[9px] ${o.side === 'buy' ? 'bg-lime-500/15 text-lime-400' : 'bg-red-500/15 text-red-400'}`}>{o.side?.toUpperCase()}</Badge>
              <ModeTag mode={o.mode} />
              <StatusTag status={o.status} />
            </div>
            <div className="flex items-center gap-2">
              {o.realized_pnl !== 0 && (
                <span className={`text-xs font-bold ${o.realized_pnl > 0 ? 'text-lime-400' : 'text-red-400'}`}>
                  {o.realized_pnl > 0 ? '+' : ''}${o.realized_pnl?.toFixed(2)}
                </span>
              )}
              {['pending', 'partially_filled', 'filled'].includes(o.status) && (
                <button onClick={() => cancelOrder(o.order_id)} className="text-slate-500 hover:text-red-400">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4 mt-1.5 text-[10px] text-slate-400">
            <span>Qty: {o.total_qty}</span>
            {o.avg_fill_price && <span>Avg: ${o.avg_fill_price?.toFixed(2)}</span>}
            {o.current_price && <span>Now: ${o.current_price?.toFixed(2)}</span>}
            {o.stop_loss && <span className="text-red-400">SL: ${o.stop_loss.price?.toFixed(2)}{o.stop_loss.trailing ? ' (trail)' : ''}{o.stop_loss.moved_to_break_even ? ' [BE]' : ''}</span>}
            {o.take_profits?.length > 0 && <span className="text-lime-400">TPs: {o.take_profits.filter(tp => tp.triggered).length}/{o.take_profits.length}</span>}
            {o.legs?.length > 1 && <span className="text-violet-400">Ladder: {o.legs.filter(l => l.filled).length}/{o.legs.length}</span>}
          </div>
        </div>
      ))
    )}
  </div>
);

export default SmartOrderList;
