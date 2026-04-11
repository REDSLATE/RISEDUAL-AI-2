import React from 'react';
import { RefreshCw } from 'lucide-react';
import { Card } from '../ui/card';

export const StatCard = ({ icon, label, value, accent }) => {
  const colors = {
    red: 'from-red-950/50 to-red-900/30 border-orange-700/40',
    blue: 'from-blue-950/50 to-blue-900/30 border-blue-800/40',
    violet: 'from-violet-950/50 to-violet-900/30 border-violet-800/40',
    emerald: 'from-emerald-950/50 to-emerald-900/30 border-lime-700/40',
  };
  return (
    <Card className={`bg-gradient-to-br ${colors[accent] || 'from-slate-800/50 to-slate-800/30 border-slate-400/30/40'} p-4 rounded-xl`}>
      <div className="flex items-center gap-2 mb-1">{icon}<span className="text-slate-300 text-xs">{label}</span></div>
      <p className="text-white text-2xl font-bold tabular-nums">{value}</p>
    </Card>
  );
};

export const LoadingState = ({ text }) => (
  <div className="bg-slate-700/55 border border-slate-400/30/40 rounded-xl p-12 flex items-center justify-center">
    <RefreshCw className="w-5 h-5 text-blue-400 animate-spin mr-3" />
    <span className="text-slate-300 text-sm">{text}</span>
  </div>
);
