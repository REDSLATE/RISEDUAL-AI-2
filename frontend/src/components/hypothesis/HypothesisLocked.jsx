import React from 'react';
import { Sparkles, Lock, Zap, BarChart3, Globe, Landmark } from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';

const HypothesisLocked = ({ hypothesis, user, onLogin, onSubscribe }) => (
  <Card className="relative bg-slate-800/50 border-slate-700/40 rounded-xl overflow-hidden" data-testid="hypothesis-locked">
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2 text-white font-semibold text-lg">
        <Sparkles className="w-5 h-5 text-[#35D6C8]" />
        AI Hypothesis Ready for {hypothesis.symbol}
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-slate-900/60 rounded-lg p-3 text-center">
          <BarChart3 className="w-4 h-4 text-blue-400 mx-auto mb-1" />
          <p className="text-white text-lg font-bold">{hypothesis.teaser.data_sources_count}</p>
          <p className="text-slate-500 text-[10px]">Data Points</p>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-3 text-center">
          <Globe className="w-4 h-4 text-emerald-400 mx-auto mb-1" />
          <p className="text-white text-lg font-bold">{hypothesis.teaser.world_events_count}</p>
          <p className="text-slate-500 text-[10px]">World Events</p>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-3 text-center">
          <Landmark className="w-4 h-4 text-violet-400 mx-auto mb-1" />
          <p className="text-white text-lg font-bold">{hypothesis.teaser.congressional_trades_count}</p>
          <p className="text-slate-500 text-[10px]">Congress Trades</p>
        </div>
      </div>
    </div>
    <div className="relative px-6 pb-6">
      <div className="blur-md select-none pointer-events-none" aria-hidden="true">
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="text-3xl font-bold text-emerald-400">BUY</span>
            <span className="text-slate-400">|</span>
            <span className="text-white text-xl font-semibold">Confidence: 78%</span>
          </div>
          <p className="text-slate-300 text-sm">Based on analysis of 19 news articles, 30 world events, 12 congressional trades, and foreign market correlations...</p>
        </div>
      </div>
      <div className="absolute inset-0 flex flex-col items-center justify-center bg-slate-900/50 backdrop-blur-sm rounded-b-xl">
        <Lock className="w-8 h-8 text-[#35D6C8] mb-3" />
        <p className="text-white font-semibold text-lg mb-1">Unlock Full AI Hypothesis</p>
        <p className="text-slate-400 text-sm text-center max-w-xs mb-4">{hypothesis.teaser.summary}</p>
        <div className="flex gap-3">
          {!user && (
            <Button onClick={onLogin} className="bg-slate-700 hover:bg-slate-600 text-white rounded-xl" data-testid="hypothesis-login-btn">
              Log In
            </Button>
          )}
          <Button onClick={onSubscribe} className="bg-[#35D6C8] hover:bg-[#67E3D3] text-white rounded-xl" data-testid="hypothesis-subscribe-btn">
            <Zap className="w-4 h-4 mr-2" /> Subscribe to Pro
          </Button>
        </div>
      </div>
    </div>
  </Card>
);

export default HypothesisLocked;
