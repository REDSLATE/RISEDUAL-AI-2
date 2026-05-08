import React from 'react';
import { Volume2, VolumeX } from 'lucide-react';

const VoiceSelector = ({ voiceMode, setVoiceMode, isSpeaking, onStopSpeaking }) => {
  const cycle = () => {
    if (isSpeaking) { onStopSpeaking(); return; }
    const order = [null, 'female', 'male'];
    const idx = order.indexOf(voiceMode);
    setVoiceMode(order[(idx + 1) % order.length]);
  };

  const label = voiceMode === 'female' ? 'F' : voiceMode === 'male' ? 'M' : '';
  const color = isSpeaking ? 'text-[#3DE8D9] animate-pulse' : voiceMode ? 'text-[#3DE8D9]' : 'text-slate-400';
  const Icon = voiceMode ? Volume2 : VolumeX;

  return (
    <button
      onClick={cycle}
      className={`relative h-7 px-1.5 flex items-center gap-0.5 rounded-md hover:bg-slate-700/50 transition-colors ${color}`}
      title={isSpeaking ? 'Stop speaking' : voiceMode ? `Voice: ${voiceMode} (click to change)` : 'Voice off (click to enable)'}
      data-testid="voice-toggle"
    >
      <Icon className="w-3.5 h-3.5" />
      {label && <span className="text-[9px] font-bold">{label}</span>}
    </button>
  );
};

export default VoiceSelector;
export { VoiceSelector };
