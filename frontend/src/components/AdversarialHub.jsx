import React from 'react';
import { ShieldCheck, ShieldAlert, BrainCircuit, Terminal } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';

const AdversarialHub = ({ prediction }) => {
  if (!prediction) return null;

  const direction = (prediction.overall_direction || 'NEUTRAL').toUpperCase();
  const confidence = prediction.confidence_score ?? prediction.confidence ?? 0;
  const consensus = prediction.agent_consensus || '';
  const timestamp = prediction.timestamp
    ? new Date(prediction.timestamp).toLocaleTimeString('en-US', { hour12: false })
    : '--:--:--';

  // Derive strategist signal
  const strategistCall = direction.includes('BULL') || direction === 'UP'
    ? 'LONG'
    : direction.includes('BEAR') || direction === 'DOWN'
      ? 'SHORT'
      : 'HOLD';

  // Derive auditor status from confidence + risk factors
  const riskCount = prediction.risk_factors?.length || 0;
  const auditorStatus = confidence >= 60 && riskCount <= 3 ? 'PASS' : 'VETO';

  // Derive failure mode
  const failureMode = auditorStatus === 'PASS'
    ? 'NO_THREAT_DETECTED'
    : riskCount > 3
      ? 'HIGH_RISK_DENSITY'
      : 'LOW_CONFIDENCE_SIGNAL';

  // Derive detection label from key signals
  const detectionLabel = prediction.key_signals?.[0]
    ? prediction.key_signals[0].split(' ').slice(0, 4).join('_').toUpperCase().replace(/[^A-Z0-9_]/g, '')
    : 'PATTERN_ANALYSIS_V4';

  const ticker = prediction.fear_greed ? 'SPY/USD' : 'SPY';

  return (
    <Card className="bg-[#0D1526] border-[#3DE8D9]/20 shadow-2xl overflow-hidden font-mono" data-testid="adversarial-hub">
      <CardHeader className="bg-[#3DE8D9]/5 border-b border-white/5 py-3 px-5">
        <div className="flex justify-between items-center">
          <CardTitle className="text-xs font-bold flex items-center gap-2 text-[#3DE8D9]">
            <BrainCircuit className="w-4 h-4" /> ADVERSARIAL PIPELINE V5.2
          </CardTitle>
          <span className="text-[10px] text-slate-400">{timestamp} UTC</span>
        </div>
      </CardHeader>

      <CardContent className="p-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 relative">

          {/* STRATEGIST SIDE */}
          <div className="space-y-3" data-testid="strategist-panel">
            <div className="flex items-center gap-2 text-blue-400 text-xs font-bold uppercase tracking-widest">
              <Terminal className="w-3 h-3" /> Strategist_Agent
            </div>
            <div className="bg-blue-500/10 border border-blue-500/20 p-4 rounded-lg">
              <div className="text-2xl font-black text-blue-400 italic tracking-tight">PROPOSE {strategistCall}</div>
              <div className="text-[10px] mt-2 text-blue-300/60">DETECTED: {detectionLabel}</div>
            </div>
          </div>

          {/* AUDITOR SIDE */}
          <div className="space-y-3" data-testid="auditor-panel">
            <div className="flex items-center gap-2 text-red-400 text-xs font-bold uppercase tracking-widest">
              <ShieldAlert className="w-3 h-3" /> Risk_Auditor_Agent
            </div>
            <div className={`p-4 rounded-lg border ${
              auditorStatus === 'PASS'
                ? 'bg-emerald-500/10 border-emerald-500/20'
                : 'bg-red-500/10 border-red-500/20'
            }`}>
              <div className={`text-2xl font-black italic tracking-tight ${
                auditorStatus === 'PASS' ? 'text-emerald-400' : 'text-red-400'
              }`}>
                {auditorStatus === 'PASS' ? (
                  <span className="flex items-center gap-2"><ShieldCheck className="w-6 h-6" /> PASS</span>
                ) : (
                  <span className="flex items-center gap-2"><ShieldAlert className="w-6 h-6" /> VETO</span>
                )}
              </div>
              <div className="text-[10px] mt-2 text-slate-400">
                MODE: {failureMode}
              </div>
            </div>
          </div>

          {/* Center pulse (adversarial clash) */}
          <div className="hidden md:block absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none">
            <div className="w-8 h-8 bg-[#0D1526] border border-[#3DE8D9]/40 rounded-full flex items-center justify-center">
              <div className="w-2 h-2 bg-[#3DE8D9] animate-ping rounded-full" />
            </div>
          </div>
        </div>

        {/* SYNTHESIZED SIGNAL */}
        <div className="mt-6 pt-5 border-t border-white/5 text-center" data-testid="synthesized-signal">
          <div className="inline-flex items-center gap-3 bg-[#3DE8D9]/10 border border-[#3DE8D9]/25 px-5 py-3 rounded-full">
            <span className="text-[10px] uppercase tracking-[0.25em] font-bold text-[#3DE8D9]">Synthesized Signal:</span>
            <span className="text-lg font-black text-white italic">{ticker} @ {confidence}% CONF</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default AdversarialHub;
