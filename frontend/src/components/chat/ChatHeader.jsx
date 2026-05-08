import React from 'react';
import { History, Plus, Minimize2, Brain } from 'lucide-react';
import { Button } from '../ui/button';
import { VoiceSelector } from './ChatComponents';

const ChatHeader = ({
  isPro, memoryEnabled, selectedImage,
  voiceMode, setVoiceMode, isSpeaking, stopSpeaking,
  showMemory, onToggleMemory, onLoadMemories,
  showPatterns, onTogglePatterns,
  showHistory, onToggleHistory, onLoadHistory,
  onNewChat, onClose,
}) => (
  <div className="flex items-center justify-between px-3 py-2 border-b border-slate-400/25 flex-shrink-0">
    <div className="flex items-center gap-2">
      <img src="/logo-ai-bright2.png" alt="RiseDualGPT" className="w-7 h-7 object-contain" />
      <div>
        <h3 className="text-white text-xs font-semibold leading-tight">RiseDualGPT</h3>
        <p className="text-slate-400 text-[9px] leading-tight">
          {isPro ? 'Pro — Unlimited' : 'Free — 5/day'}
          {memoryEnabled && isPro && ' · Memory ON'}
          {selectedImage && ' · Image'}
        </p>
      </div>
    </div>
    <div className="flex items-center gap-0.5">
      <VoiceSelector voiceMode={voiceMode} setVoiceMode={setVoiceMode} isSpeaking={isSpeaking} onStopSpeaking={stopSpeaking} />
      {isPro && (
        <Button size="sm" variant="ghost"
          className={`h-7 w-7 p-0 ${memoryEnabled ? 'text-[#3DE8D9]' : 'text-slate-400'} hover:text-white`}
          onClick={() => { onToggleMemory(); if (!showMemory) onLoadMemories(); }}
          title={memoryEnabled ? 'Memory ON — click to manage' : 'Memory OFF — click to manage'}
          data-testid="memory-toggle-btn">
          <Brain className="w-3.5 h-3.5" />
        </Button>
      )}
      <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-7 px-2 text-[11px]" onClick={onTogglePatterns} data-testid="patterns-toggle">
        Patterns
      </Button>
      <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-7 w-7 p-0" onClick={() => { onToggleHistory(); if (!showHistory) onLoadHistory(); }} data-testid="history-toggle">
        <History className="w-3.5 h-3.5" />
      </Button>
      <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-7 w-7 p-0" onClick={onNewChat} data-testid="new-chat">
        <Plus className="w-3.5 h-3.5" />
      </Button>
      <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-7 w-7 p-0" onClick={onClose} data-testid="chat-close">
        <Minimize2 className="w-3.5 h-3.5" />
      </Button>
    </div>
  </div>
);

export default ChatHeader;
