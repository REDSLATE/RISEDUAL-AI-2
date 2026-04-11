import React from 'react';
import { Landmark, Shield, BarChart3, Clock, ChevronRight } from 'lucide-react';
import { Card } from '../ui/card';
import ProBlurWall from '../ProBlurWall';
import { StatCard, LoadingState } from './MacroShared';

const CongressTab = ({ data, loading, isPro, onSubscribe }) => {
  if (loading || !data) return <LoadingState text="Fetching government filings..." />;

  const { congressional_trades = [], fed_announcements = [], insider_trades = [] } = data;

  return (
    <div className="space-y-5" data-testid="congress-tab">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 sm:gap-4">
        <StatCard icon={<Landmark className="w-4 h-4 text-violet-400" />} label="Congressional Trades" value={data.congressional_count || 0} accent="violet" />
        <StatCard icon={<Shield className="w-4 h-4 text-blue-400" />} label="Fed Announcements" value={data.fed_count || 0} accent="blue" />
        <StatCard icon={<BarChart3 className="w-4 h-4 text-emerald-400" />} label="SEC Insider Filings" value={data.insider_count || 0} accent="emerald" />
      </div>

      {congressional_trades.length > 0 && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl overflow-hidden">
          <div className="p-4 border-b border-slate-700/40">
            <h3 className="text-white text-sm font-semibold flex items-center gap-2">
              <Landmark className="w-4 h-4 text-violet-400" /> Recent Congressional Stock Trades
            </h3>
          </div>
          <ProBlurWall freeRowCount={3} onSubscribe={onSubscribe} label="Congressional Trades">
          <div className="overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0 overflow-hidden" style={{maxHeight: isPro ? 'none' : '320px'}}>
            <table className="w-full text-sm" data-testid="congress-trades-table">
              <thead>
                <tr className="border-b border-slate-700/40">
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Representative</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Party</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Ticker</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Type</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Amount</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Date</th>
                </tr>
              </thead>
              <tbody>
                {congressional_trades.map((trade, i) => (
                  <tr key={trade.ticker ? `${trade.representative}-${trade.ticker}-${i}` : i} className="border-b border-slate-800/40 hover:bg-slate-700/20 transition-colors">
                    <td className="px-4 py-2.5 text-white font-medium">{trade.representative || 'N/A'}</td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${getPartyStyle(trade.party)}`}>
                        {getPartyLabel(trade.party, trade.chamber)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-[#35D6C8] font-bold">{trade.ticker || '\u2014'}</span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`text-xs font-semibold uppercase ${getTradeTypeColor(trade.type)}`}>{trade.type || '\u2014'}</span>
                    </td>
                    <td className="px-4 py-2.5 text-slate-300 text-xs">{trade.amount || '\u2014'}</td>
                    <td className="px-4 py-2.5 text-slate-500 text-xs">{trade.transaction_date || '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </ProBlurWall>
        </Card>
      )}

      {fed_announcements.length > 0 && (
        <div>
          <h3 className="text-slate-300 text-sm font-semibold mb-3 flex items-center gap-2">
            <Shield className="w-4 h-4 text-blue-400" /> Federal Reserve Announcements
          </h3>
          <div className="space-y-2">
            {fed_announcements.map((ann) => (
              <Card key={ann.title || ann.date} className="bg-slate-800/40 border-slate-700/30 rounded-xl p-3 hover:border-slate-600 transition-all">
                <p className="text-white text-sm font-medium leading-snug">{ann.title}</p>
                <div className="flex items-center gap-2 mt-2">
                  <Clock className="w-3 h-3 text-slate-500" />
                  <span className="text-[10px] text-slate-500">{ann.date}</span>
                  {ann.url && (
                    <a href={ann.url} target="_blank" rel="noopener noreferrer" className="ml-auto text-[10px] text-[#35D6C8] hover:underline flex items-center gap-1">
                      View <ChevronRight className="w-3 h-3" />
                    </a>
                  )}
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {insider_trades.length > 0 && (
        <div>
          <h3 className="text-slate-300 text-sm font-semibold mb-3 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-emerald-400" /> SEC Insider Filings
          </h3>
          <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1 custom-scrollbar">
            {insider_trades.map((t, i) => (
              <Card key={`insider-${t.company || t.ticker || i}-${i}`} className="bg-slate-800/40 border-slate-700/30 rounded-xl p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white text-sm font-medium">{t.company || t.ticker || 'Unknown'}</p>
                    <p className="text-slate-500 text-xs mt-0.5">{t.description}</p>
                  </div>
                  <span className="text-slate-500 text-xs">{t.filed_date}</span>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const getPartyStyle = (party) => {
  if (party === 'R') return 'bg-red-900/30 text-red-400';
  if (party === 'D') return 'bg-blue-900/30 text-blue-400';
  return 'bg-slate-700 text-slate-400';
};
const getPartyLabel = (party, chamber) => {
  const label = party || '\u2014';
  return chamber ? `${label} \u00B7 ${chamber}` : label;
};
const getTradeTypeColor = (type) => {
  const lower = type?.toLowerCase();
  if (lower === 'buy' || lower === 'purchase') return 'text-emerald-400';
  return 'text-red-400';
};

export default CongressTab;
