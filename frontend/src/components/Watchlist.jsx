import React, { useState } from 'react';
import { Card } from './ui/card';
import useWatchlistData from '../hooks/useWatchlistData';
import WatchlistToolbar from './watchlist/WatchlistToolbar';
import WatchlistTable from './watchlist/WatchlistTable';
import ShareBoardLeaderboard from './ShareBoardLeaderboard';

/**
 * Watchlist card — thin orchestrator. Delegates:
 *   - All state + fetching to `useWatchlistData`
 *   - Header/add-input UI to `WatchlistToolbar`
 *   - Rows + Smart Money shift alerts to `WatchlistTable`
 */
const Watchlist = ({ onSubscribe }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const {
    user, isPro,
    watchlist, smartScores, smsShifts, smsHistory,
    newSymbol, setNewSymbol,
    capWarning, refreshing,
    addSymbol, removeSymbol, refreshQuotes,
  } = useWatchlistData(isExpanded);

  return (
    <Card className="bg-slate-700/60 border-slate-400/25 rounded-xl p-4">
      <WatchlistToolbar
        watchlist={watchlist}
        smartScores={smartScores}
        smsHistory={smsHistory}
        user={user}
        isPro={isPro}
        isExpanded={isExpanded}
        setIsExpanded={setIsExpanded}
        newSymbol={newSymbol}
        setNewSymbol={setNewSymbol}
        addSymbol={addSymbol}
        capWarning={capWarning}
        refreshing={refreshing}
        refreshQuotes={refreshQuotes}
        onSubscribe={onSubscribe}
      />

      {isExpanded && (
        <>
          <WatchlistTable
            watchlist={watchlist}
            smartScores={smartScores}
            smsShifts={smsShifts}
            smsHistory={smsHistory}
            onRemove={removeSymbol}
          />
          <ShareBoardLeaderboard authenticated={!!user} />
        </>
      )}
    </Card>
  );
};

export default Watchlist;
