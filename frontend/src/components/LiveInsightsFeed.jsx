import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Activity, CheckCircle, XCircle, AlertTriangle, Brain, Zap, Radio, ChevronDown, ChevronUp } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

const FAILURE_LABELS = {
  TECH_FAKEOUT: { label: 'Fakeout', color: 'text-amber-400', bg: 'bg-amber-500/10 border-amber-500/20' },
  MACRO_SHOCK: { label: 'Macro Shock', color: 'text-red-400', bg: 'bg-red-500/10 border-red-500/20' },
  LIQUIDITY_GAP: { label: 'Liquidity', color: 'text-purple-400', bg: 'bg-purple-500/10 border-purple-500/20' },
  REGIME_SHIFT: { label: 'Regime Shift', color: 'text-cyan-400', bg: 'bg-cyan-500/10 border-cyan-500/20' },
  UNKNOWN: { label: 'Unknown', color: 'text-slate-400', bg: 'bg-slate-500/10 border-slate-500/20' },
};

const EventIcon = ({ type, correct }) => {
  if (type === 'new_verification') {
    return correct
      ? <CheckCircle className="w-4 h-4 text-emerald-400" />
      : <XCircle className="w-4 h-4 text-red-400" />;
  }
  if (type === 'post_mortem') return <Brain className="w-4 h-4 text-[#35D6C8]" />;
  if (type === 'toxic_alert') return <AlertTriangle className="w-4 h-4 text-red-400" />;
  if (type === 'memory_update') return <Zap className="w-4 h-4 text-amber-400" />;
  return <Activity className="w-4 h-4 text-slate-400" />;
};

const FailureBadge = ({ code }) => {
  const info = FAILURE_LABELS[code] || FAILURE_LABELS.UNKNOWN;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${info.bg} ${info.color}`}>
      {info.label}
    </span>
  );
};

const formatTime = (ts) => {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const diffMs = now - d;
  if (diffMs < 60000) return 'just now';
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}m ago`;
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
};

const VerificationEvent = ({ data }) => (
  <div className="flex items-start gap-2.5" data-testid="insight-verification">
    <EventIcon type="new_verification" correct={data.correct} />
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2">
        <span className="text-white text-sm font-semibold">{data.ticker}</span>
        <span className={`text-xs ${data.correct ? 'text-emerald-400' : 'text-red-400'}`}>
          {data.correct ? 'HIT' : 'MISS'}
        </span>
        {!data.correct && data.failure_code && <FailureBadge code={data.failure_code} />}
      </div>
      <p className="text-slate-400 text-xs mt-0.5">
        {data.direction} @ ${data.price_at?.toFixed(2)} {'\u2192'} ${data.price_now?.toFixed(2)}
        {data.confidence > 0 && <span className="text-slate-400 ml-1">({data.confidence}% conf)</span>}
      </p>
    </div>
  </div>
);

const PostMortemEvent = ({ data }) => (
  <div className="flex items-start gap-2.5" data-testid="insight-postmortem">
    <EventIcon type="post_mortem" />
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2">
        <span className="text-white text-sm font-semibold">{data.ticker}</span>
        <span className="text-[#35D6C8] text-xs">Post-Mortem</span>
        {data.failure_code && <FailureBadge code={data.failure_code} />}
      </div>
      {data.reasoning && (
        <p className="text-slate-400 text-xs mt-0.5 line-clamp-2">{data.reasoning}</p>
      )}
      {data.key_headline && data.key_headline !== 'None' && (
        <p className="text-slate-400 text-[10px] mt-0.5 italic truncate">"{data.key_headline}"</p>
      )}
    </div>
  </div>
);

const ToxicAlertEvent = ({ data }) => (
  <div className="flex items-start gap-2.5" data-testid="insight-toxic">
    <EventIcon type="toxic_alert" />
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2">
        <span className="text-red-400 text-sm font-semibold">Toxic Spikes</span>
        <span className="text-red-400/70 text-xs">{data.toxic_count} detected</span>
      </div>
      {data.affected_tickers?.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {data.affected_tickers.slice(0, 4).map(t => (
            <span key={t} className="text-[10px] bg-red-500/10 text-red-400 border border-red-500/20 rounded px-1.5 py-0.5">{t}</span>
          ))}
          {data.affected_tickers.length > 4 && (
            <span className="text-[10px] text-slate-400">+{data.affected_tickers.length - 4}</span>
          )}
        </div>
      )}
    </div>
  </div>
);

const EventItem = ({ event }) => {
  const { type, data, timestamp } = event;
  return (
    <div className="px-4 py-3 border-b border-slate-600/30/50 hover:bg-slate-600/30/20 transition-colors">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          {type === 'new_verification' && <VerificationEvent data={data} />}
          {type === 'post_mortem' && <PostMortemEvent data={data} />}
          {type === 'toxic_alert' && <ToxicAlertEvent data={data} />}
        </div>
        <span className="text-slate-400 text-[10px] whitespace-nowrap flex-shrink-0">{formatTime(timestamp)}</span>
      </div>
    </div>
  );
};

const LiveInsightsFeed = () => {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const eventSourceRef = useRef(null);
  const reconnectTimer = useRef(null);

  const connectSSE = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const es = new EventSource(`${BACKEND_URL}/api/stream/insights`);
    eventSourceRef.current = es;

    es.addEventListener('connected', () => {
      setConnected(true);
    });

    es.addEventListener('new_verification', (e) => {
      const data = JSON.parse(e.data);
      setEvents(prev => [{ type: 'new_verification', data, timestamp: new Date().toISOString() }, ...prev].slice(0, 50));
    });

    es.addEventListener('post_mortem', (e) => {
      const data = JSON.parse(e.data);
      setEvents(prev => [{ type: 'post_mortem', data, timestamp: new Date().toISOString() }, ...prev].slice(0, 50));
    });

    es.addEventListener('toxic_alert', (e) => {
      const data = JSON.parse(e.data);
      setEvents(prev => [{ type: 'toxic_alert', data, timestamp: new Date().toISOString() }, ...prev].slice(0, 50));
    });

    es.addEventListener('heartbeat', () => {
      setConnected(true);
    });

    es.onerror = () => {
      setConnected(false);
      es.close();
      reconnectTimer.current = setTimeout(connectSSE, 5000);
    };
  }, []);

  useEffect(() => {
    // Load recent events first, then connect SSE
    fetch(`${BACKEND_URL}/api/stream/recent?limit=15`)
      .then(r => r.json())
      .then(data => {
        if (data.events?.length > 0) {
          setEvents(data.events.filter(e =>
            ['new_verification', 'post_mortem', 'toxic_alert'].includes(e.type)
          ));
        }
      })
      .catch(() => {});

    connectSSE();

    return () => {
      if (eventSourceRef.current) eventSourceRef.current.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [connectSSE]);

  const verifications = events.filter(e => e.type === 'new_verification');
  const hits = verifications.filter(e => e.data.correct).length;
  const misses = verifications.filter(e => !e.data.correct).length;

  return (
    <div className="bg-[#0B1120] border border-slate-800/60 rounded-xl overflow-hidden" data-testid="live-insights-feed">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-600/30/20 transition-colors"
        data-testid="insights-feed-toggle"
      >
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Radio className={`w-4 h-4 ${connected ? 'text-emerald-400 animate-pulse' : 'text-slate-400'}`} />
            <h3 className="text-white text-sm font-semibold tracking-wide">Live Insights Feed</h3>
          </div>
          {connected && (
            <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-full px-2 py-0.5">
              LIVE
            </span>
          )}
        </div>

        <div className="flex items-center gap-3">
          {verifications.length > 0 && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-emerald-400">{hits}H</span>
              <span className="text-slate-400">/</span>
              <span className="text-red-400">{misses}M</span>
            </div>
          )}
          {expanded ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
        </div>
      </button>

      {/* Events List */}
      {expanded && (
        <div className="max-h-[320px] overflow-y-auto border-t border-slate-600/30/50" data-testid="insights-feed-list">
          {events.length === 0 ? (
            <div className="py-8 text-center">
              <Activity className="w-6 h-6 text-slate-700 mx-auto mb-2" />
              <p className="text-slate-400 text-xs">Waiting for new predictions and verifications...</p>
              <p className="text-slate-700 text-[10px] mt-1">Events stream in real-time as predictions are verified</p>
            </div>
          ) : (
            events.map((evt, i) => <EventItem key={`${evt.type}-${evt.timestamp}-${i}`} event={evt} />)
          )}
        </div>
      )}
    </div>
  );
};

export default LiveInsightsFeed;
