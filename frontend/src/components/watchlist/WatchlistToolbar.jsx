import React from 'react';
import { Star, Plus, Lock, RefreshCw } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import InfoTooltip from '../InfoTooltip';
import ShareSmartMoneyBoard from '../ShareSmartMoneyBoard';
import { FREE_WATCHLIST_LIMIT } from '../../hooks/useWatchlistData';

/**
 * Header strip + add-ticker input + capacity warning for the Watchlist card.
 * Purely presentational; all state + actions come in via props.
 */
const WatchlistToolbar = ({
  watchlist,
  smartScores,
  smsHistory,
  user,
  isPro,
  isExpanded,
  setIsExpanded,
  newSymbol,
  setNewSymbol,
  addSymbol,
  capWarning,
  refreshing,
  refreshQuotes,
  onSubscribe,
}) => (
  <>
    <div className="flex items-center justify-between mb-4">
      <div className="flex items-center gap-2">
        <Star className="w-5 h-5 text-yellow-500 fill-yellow-500" />
        <h3 className="text-white font-semibold">My Watchlist</h3>
        <InfoTooltip id="watchlist" />
        <span className="text-slate-300 text-sm">
          ({watchlist.length}{user && !isPro ? `/${FREE_WATCHLIST_LIMIT}` : ''})
        </span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setIsExpanded(!isExpanded)}
        className="text-slate-400 hover:text-slate-50"
      >
        {isExpanded ? 'Collapse' : 'Expand'}
      </Button>
      {isExpanded && watchlist.length > 0 && (
        <div className="flex items-center gap-1 ml-1">
          <ShareSmartMoneyBoard
            watchlist={watchlist}
            smartScores={smartScores}
            smsHistory={smsHistory}
            userId={user?.id || user?._id || user?.email}
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={refreshQuotes}
            disabled={refreshing}
            className="text-slate-400 hover:text-slate-50"
            data-testid="watchlist-refresh-btn"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      )}
    </div>

    {isExpanded && (
      <>
        <div className="flex gap-2 mb-2">
          <Input
            placeholder="Add symbol (e.g., AAPL)"
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && addSymbol()}
            className="bg-[#1E293B] border-slate-600 text-white"
            data-testid="watchlist-input"
          />
          <Button onClick={addSymbol} className="bg-[#3DE8D9] hover:bg-[#7AEEE0]" data-testid="watchlist-add-btn">
            <Plus className="w-4 h-4" />
          </Button>
        </div>
        {capWarning && (
          <div
            className="flex items-center gap-2 mb-3 bg-amber-900/20 border border-amber-800/40 rounded-lg px-3 py-2"
            data-testid="watchlist-cap-warning"
          >
            <Lock className="w-3.5 h-3.5 text-amber-300 flex-shrink-0" />
            <p className="text-amber-300 text-xs flex-1">{capWarning}</p>
            {onSubscribe && (
              <Button
                size="sm"
                className="bg-[#3DE8D9] text-white text-xs h-6 px-2 rounded-lg"
                onClick={onSubscribe}
              >
                Upgrade
              </Button>
            )}
          </div>
        )}
      </>
    )}
  </>
);

export default WatchlistToolbar;
