import React, { useState, useEffect, useCallback } from 'react';
import { TrendingUp, TrendingDown, Brain, Sparkles } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { HeatmapHeader } from './heatmap/HeatmapHeader';
import HeatmapLegend from './heatmap/HeatmapLegend';
import { SectorTile } from './heatmap/SectorTile';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

const SectorHeatmap = () => {
  const [data, setData] = useState(null);
  const [sentiment, setSentiment] = useState(null);
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sentimentLoading, setSentimentLoading] = useState(false);
  const [period, setPeriod] = useState('change_1d');
  const [error, setError] = useState('');

  const fetchData = useCallback(async (force = false) => {
    setLoading(true);
    setError('');
    try {
      const url = force ? `${BACKEND_URL}/api/sectors/heatmap?force=true` : `${BACKEND_URL}/api/sectors/heatmap`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Server error (${res.status})`);
      const json = await res.json();
      if (!json.sectors?.length) throw new Error('No sector data returned');
      setData(json);
    } catch (e) {
      setError(e.message || 'Failed to fetch sector data');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSentiment = useCallback(async (force = false) => {
    setSentimentLoading(true);
    try {
      const url = force ? `${BACKEND_URL}/api/sectors/sentiment?force=true` : `${BACKEND_URL}/api/sectors/sentiment`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Sentiment error (${res.status})`);
      setSentiment(await res.json());
      try {
        const hRes = await fetch(`${BACKEND_URL}/api/sectors/sentiment/history?limit=20`);
        if (hRes.ok) setHistory(await hRes.json());
      } catch (e) { console.warn('Sentiment history fetch failed:', e); }
    } catch (e) {
      console.error('Sentiment fetch failed:', e);
    } finally {
      setSentimentLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 120000);
    return () => clearInterval(interval);
  }, [fetchData]);

  useEffect(() => {
    if (period === 'ai_sentiment' && !sentiment && !sentimentLoading) {
      fetchSentiment();
    }
  }, [period, sentiment, sentimentLoading, fetchSentiment]);

  const isAI = period === 'ai_sentiment';

  if (loading && !data) {
    return (
      <Card className="bg-slate-700/60 border-slate-400/30/30 rounded-2xl p-5" data-testid="sector-heatmap-skeleton">
        <div className="flex items-center gap-3 mb-5">
          <div className="skeleton w-10 h-10 rounded-xl" />
          <div><div className="skeleton w-48 h-5 mb-2" /><div className="skeleton w-32 h-3" /></div>
        </div>
        <div className="flex gap-2 mb-5">
          {[1,2,3,4,5,6].map(n => <div key={`skel-tab-${n}`} className="skeleton w-12 h-7 rounded-lg" />)}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {[1,2,3,4,5,6,7,8].map(n => <div key={`skel-card-${n}`} className="skeleton h-24 rounded-xl" />)}
        </div>
      </Card>
    );
  }

  if (error) {
    return <div className="bg-orange-900 border border-orange-700/40 rounded-xl p-4 text-orange-400 text-sm">{error}</div>;
  }

  const sectors = data?.sectors || [];
  const summary = data?.market_summary || {};
  const sentimentData = sentiment?.sectors || {};
  const sectorTrends = history?.sector_trends || {};

  return (
    <div data-testid="sector-heatmap">
      <HeatmapHeader
        period={period} setPeriod={setPeriod} isAI={isAI}
        loading={loading} sentimentLoading={sentimentLoading}
        onRefresh={() => isAI ? fetchSentiment(true) : fetchData(true)}
      />

      {/* AI Sentiment Header Bar */}
      {isAI && sentiment && (
        <div className="mb-4 p-3 rounded-xl bg-purple-950/20 border border-purple-800/30">
          <div className="flex items-center gap-2 mb-1.5">
            <Sparkles className="w-4 h-4 text-purple-400" />
            <span className="text-purple-300 text-xs font-semibold uppercase tracking-wide">Multi-Agent Consensus</span>
            <Badge className="bg-purple-900/50 text-purple-300 border-purple-700/50 text-[10px]">{sentiment.agents_used || 4} agents</Badge>
            <Badge className={`text-[10px] border-0 ${sentiment.risk_regime === 'risk-on' ? 'bg-lime-600 text-lime-300' : sentiment.risk_regime === 'risk-off' ? 'bg-orange-700 text-orange-300' : 'bg-slate-700/50 text-slate-300'}`}>
              {sentiment.risk_regime || 'mixed'}
            </Badge>
            {history && history.snapshots_count > 1 && (
              <Badge className="bg-slate-700/50 text-slate-400 text-[10px]">{history.snapshots_count} snapshots</Badge>
            )}
          </div>
          {sentiment.rotation_call && <p className="text-slate-300 text-xs leading-relaxed">{sentiment.rotation_call}</p>}
        </div>
      )}

      {isAI && sentimentLoading && !sentiment && (
        <div className="mb-4 p-6 rounded-xl bg-purple-950/20 border border-purple-800/30 flex flex-col items-center gap-3">
          <div className="relative">
            <Brain className="w-8 h-8 text-purple-400 animate-pulse" />
            <Sparkles className="w-4 h-4 text-purple-300 absolute -top-1 -right-1 animate-bounce" />
          </div>
          <p className="text-purple-300 text-sm font-medium">AI agents analyzing sectors...</p>
          <p className="text-slate-300 text-xs">3 analysts + 1 strategist running in parallel</p>
        </div>
      )}

      {!isAI && summary.best_sector && summary.worst_sector && (
        <div className="grid grid-cols-2 gap-3 mb-4">
          <Card className="bg-green-600 border-green-400/30 rounded-xl p-3 flex items-center gap-3">
            <TrendingUp className="w-5 h-5 text-white" />
            <div>
              <p className="text-green-100 text-[10px] uppercase">Best Sector</p>
              <p className="text-white text-sm font-bold">{summary.best_sector.name} <span className="text-lime-300">+{summary.best_sector.change}%</span></p>
            </div>
          </Card>
          <Card className="bg-red-500 border-red-400/30 rounded-xl p-3 flex items-center gap-3">
            <TrendingDown className="w-5 h-5 text-white" />
            <div>
              <p className="text-red-100 text-[10px] uppercase">Worst Sector</p>
              <p className="text-white text-sm font-bold">{summary.worst_sector.name} <span className="text-yellow-300">{summary.worst_sector.change}%</span></p>
            </div>
          </Card>
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2" data-testid="heatmap-grid">
        {sectors.map(s => (
          <SectorTile key={s.symbol} sector={s} isAI={isAI} period={period} sentimentData={sentimentData} sectorTrends={sectorTrends} />
        ))}
      </div>

      <HeatmapLegend isAI={isAI} />
    </div>
  );
};

export default SectorHeatmap;
