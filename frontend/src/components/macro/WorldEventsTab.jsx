import React from 'react';
import { Zap, AlertTriangle, Shield } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { StatCard, LoadingState } from './MacroShared';

const WorldEventsTab = ({ data, loading }) => {
  if (loading || !data) return <LoadingState text="Scanning world events..." />;

  const { high_impact_events = [], all_events = [], affected_sectors = [] } = data;

  return (
    <div className="space-y-5" data-testid="world-events-tab">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 sm:gap-4">
        <StatCard icon={<Zap className="w-4 h-4 text-amber-400" />} label="Total Events" value={data.total_events || 0} />
        <StatCard icon={<AlertTriangle className="w-4 h-4 text-red-400" />} label="High Impact" value={data.high_impact_count || 0} accent="red" />
        <StatCard icon={<Shield className="w-4 h-4 text-blue-400" />} label="Sectors Affected" value={affected_sectors.length} accent="blue" />
      </div>

      {affected_sectors.length > 0 && (
        <Card className="bg-slate-800/50 border-slate-700/40 p-4 rounded-xl">
          <h3 className="text-white text-sm font-semibold mb-3">Sector Impact Map</h3>
          <div className="flex flex-wrap gap-2">
            {affected_sectors.map((s) => (
              <div key={s.sector} className="flex items-center gap-2 bg-slate-900/60 border border-slate-700/50 rounded-lg px-3 py-2" data-testid={`sector-${s.sector}`}>
                <div className={`w-2 h-2 rounded-full ${s.avg_impact >= 75 ? 'bg-red-500' : s.avg_impact >= 50 ? 'bg-amber-500' : 'bg-emerald-500'}`} />
                <span className="text-white text-xs font-medium">{s.sector}</span>
                <span className={`text-xs font-bold ${s.avg_impact >= 75 ? 'text-red-400' : s.avg_impact >= 50 ? 'text-amber-400' : 'text-emerald-400'}`}>{s.avg_impact}</span>
                <div className="flex gap-1 ml-1">
                  {(s.tickers || []).slice(0, 3).map(t => (
                    <span key={t} className="text-[10px] text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded">{t}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {high_impact_events.length > 0 && (
        <div>
          <h3 className="text-red-400 text-sm font-semibold mb-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> High Impact Events
          </h3>
          <div className="space-y-2">
            {high_impact_events.map((e, i) => (
              <EventCard key={e.title || i} event={e} isHighImpact />
            ))}
          </div>
        </div>
      )}

      <div>
        <h3 className="text-slate-300 text-sm font-semibold mb-3">All Events ({all_events.length})</h3>
        <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1 custom-scrollbar">
          {all_events.map((e, i) => (
            <EventCard key={e.title || i} event={e} />
          ))}
        </div>
      </div>
    </div>
  );
};

const EventCard = ({ event, isHighImpact }) => (
  <Card className={`p-3 rounded-xl border transition-all hover:border-slate-600 ${
    isHighImpact ? 'bg-red-950/20 border-red-900/40' : 'bg-slate-800/40 border-slate-700/30'
  }`}>
    <div className="flex items-start justify-between gap-3">
      <div className="flex-1 min-w-0">
        <p className="text-white text-sm font-medium leading-snug truncate">{event.title}</p>
        <div className="flex items-center gap-2 mt-1.5">
          <span className="text-[10px] text-slate-500 bg-slate-800 px-2 py-0.5 rounded-full">{event.source}</span>
          {event.published && <span className="text-[10px] text-slate-500">{event.published}</span>}
        </div>
      </div>
      {event.affected_sectors?.length > 0 && (
        <div className="flex gap-1 flex-shrink-0">
          {event.affected_sectors.slice(0, 2).map((s) => (
            <Badge key={s.sector} variant="outline" className={`text-[10px] border-slate-700 ${
              s.impact_score >= 50 ? 'text-amber-400 border-amber-800/50' : 'text-slate-400'
            }`}>{s.sector}</Badge>
          ))}
        </div>
      )}
    </div>
  </Card>
);

export default WorldEventsTab;
