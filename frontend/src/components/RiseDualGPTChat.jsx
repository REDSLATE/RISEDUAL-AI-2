import React, { useState, useCallback, useEffect } from 'react';
import { MessageSquare } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { ChatMessages, ChatInputArea } from './chat/ChatComponents';
import ChatHeader from './chat/ChatHeader';
import MemoryPanel from './chat/MemoryPanel';
import ChatHistorySidebar from './chat/ChatHistorySidebar';
import AgentTrace from './chat/AgentTrace';
import ChartPatternLibrary from './ChartPatternLibrary';
import useChat from '../hooks/useChat';
import logger from '../utils/logger';

const RiseDualGPTChat = ({ onLimitReached }) => {
  const { isPro } = useAuth();
  const chat = useChat({ isPro, onLimitReached });
  const {
    messages, setMessages,
    input, setInput,
    loading,
    chatHistory,
    selectedImage, imagePreview,
    inputRef,
    sendMessage,
    loadHistory, loadSession, newChat,
    handleImageSelect, clearImage, handleSuggestionClick,
    tts: { voiceMode, setVoiceMode, isSpeaking, stopSpeaking },
    memory: { memories, memoryEnabled, loadMemories, toggleMemory, deleteMemoryItem, clearAllMemories, pinToMemory },
    agentTrace,
    API,
  } = chat;

  // Pure view state — kept in the component.
  const [isOpen, setIsOpen] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showPatterns, setShowPatterns] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [showMemory, setShowMemory] = useState(false);

  // Listen for external "open chat" requests (from watchlist "Ask AI" etc).
  useEffect(() => {
    const handler = (e) => {
      setIsOpen(true);
      const detail = e?.detail;
      if (detail?.prefill) {
        setInput(detail.prefill);
        if (detail.autoSend) {
          setTimeout(() => {
            window.dispatchEvent(new CustomEvent('risedualai-autosend'));
          }, 400);
        }
      }
    };
    window.addEventListener('risedualai-open-chat', handler);
    return () => window.removeEventListener('risedualai-open-chat', handler);
  }, [setInput]);

  // Autosend hook — fires `sendMessage` once input is set
  useEffect(() => {
    const handler = () => {
      if (input.trim()) sendMessage();
    };
    window.addEventListener('risedualai-autosend', handler);
    return () => window.removeEventListener('risedualai-autosend', handler);
    // sendMessage dependency intentionally omitted — always uses latest via closure
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  const handleCopy = useCallback((idx, text) => {
    navigator.clipboard.writeText(text);
    setCopiedId(idx);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  const handlePatternSelect = useCallback((pattern) => {
    setInput(`Analyze ${pattern.name} chart pattern: When does it typically form? What's the expected breakout direction and target? Current success rate?`);
    setShowPatterns(false);
    inputRef.current?.focus();
  }, [setInput, inputRef]);

  const handleLoadSession = useCallback(async (sid) => {
    await loadSession(sid);
    setShowHistory(false);
  }, [loadSession]);

  const handleNewChat = useCallback(() => {
    newChat();
    setShowHistory(false);
  }, [newChat]);

  const handleActionClick = useCallback((act, msgIdx) => {
    // Level-2 deep-link action — route to the target hub + close the chat.
    const ctx = typeof window !== 'undefined' ? (window.__risedualActiveView || null) : null;
    try {
      fetch(`${API}/analytics/chip-event`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          action: 'action-clicked',
          chip_text: act.label,
          message_idx: typeof msgIdx === 'number' ? msgIdx : null,
          context_hub: ctx,
        }),
      }).catch(() => { /* silent */ });
    } catch { /* silent */ }

    try {
      if (act.kind === 'research') {
        window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: 'research' } }));
        if (act.ticker) {
          setTimeout(() => {
            window.dispatchEvent(new CustomEvent('risedualai-research', { detail: act.ticker }));
          }, 150);
        }
      } else if (act.kind === 'watchlist' && act.ticker) {
        window.dispatchEvent(new CustomEvent('risedualai-add-watchlist', { detail: act.ticker }));
      } else if (act.kind === 'warroom' || act.kind === 'options' || act.kind === 'workspace' || act.kind === 'dashboard') {
        window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: act.kind } }));
      }
    } catch (err) {
      logger.warn('Action dispatch failed:', err);
    }
    setIsOpen(false);
  }, [API]);

  const handleFollowupClick = useCallback((chip, msgIdx) => {
    try {
      fetch(`${API}/analytics/chip-event`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          action: 'clicked',
          chip_text: chip,
          message_idx: typeof msgIdx === 'number' ? msgIdx : null,
          context_hub: typeof window !== 'undefined' ? (window.__risedualActiveView || null) : null,
        }),
      }).catch(() => { /* silent */ });
    } catch { /* silent */ }
    setInput(chip);
    setTimeout(() => sendMessage(), 0);
  }, [API, setInput, sendMessage]);

  return (
    <>
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="fixed bottom-20 lg:bottom-5 right-5 z-[60] w-14 h-14 rounded-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white shadow-lg shadow-[#3DE8D9]/30 flex items-center justify-center transition-all hover:scale-105"
          data-testid="chat-fab"
        >
          <MessageSquare className="w-6 h-6" />
        </button>
      )}

      {isOpen && (
        <div className="fixed bottom-16 right-3 left-3 lg:left-auto lg:bottom-6 lg:right-6 z-[60] w-auto lg:w-[400px] lg:max-w-[calc(100vw-3rem)] h-[72dvh] max-h-[620px] lg:h-[560px] lg:max-h-[calc(100vh-7rem)] flex flex-col bg-[#060E1F]/95 backdrop-blur-md rounded-2xl border border-slate-400/30 shadow-[0_20px_60px_-15px_rgba(0,0,0,0.75)] overflow-hidden" data-testid="risedual-gpt-chat">
          <ChatHeader
            isPro={isPro} memoryEnabled={memoryEnabled} selectedImage={selectedImage}
            voiceMode={voiceMode} setVoiceMode={setVoiceMode} isSpeaking={isSpeaking} stopSpeaking={stopSpeaking}
            showMemory={showMemory} onToggleMemory={() => setShowMemory(!showMemory)} onLoadMemories={loadMemories}
            showPatterns={showPatterns} onTogglePatterns={() => setShowPatterns(!showPatterns)}
            showHistory={showHistory} onToggleHistory={() => setShowHistory(!showHistory)} onLoadHistory={loadHistory}
            onNewChat={handleNewChat} onClose={() => setIsOpen(false)}
          />

          {showHistory && (
            <ChatHistorySidebar
              history={chatHistory}
              onSelect={handleLoadSession}
              onClose={() => setShowHistory(false)}
            />
          )}

          {showMemory && (
            <MemoryPanel
              memories={memories}
              memoryEnabled={memoryEnabled}
              onToggle={toggleMemory}
              onDelete={deleteMemoryItem}
              onClearAll={clearAllMemories}
              onClose={() => setShowMemory(false)}
            />
          )}

          {showPatterns && (
            <div className="border-b border-slate-400/25 max-h-[240px] overflow-y-auto flex-shrink-0">
              <ChartPatternLibrary onPatternSelect={handlePatternSelect} compact />
            </div>
          )}

          <AgentTrace trace={agentTrace} />

          <ChatMessages
            messages={messages}
            showPatterns={showPatterns}
            copiedId={copiedId}
            onCopy={handleCopy}
            isPro={isPro}
            onPin={(content) => pinToMemory(content, setMessages)}
            onSuggestionClick={handleSuggestionClick}
            onActionClick={handleActionClick}
            onFollowupClick={handleFollowupClick}
          />

          <ChatInputArea
            input={input}
            setInput={setInput}
            onSend={sendMessage}
            loading={loading}
            imagePreview={imagePreview}
            onImageSelect={handleImageSelect}
            onClearImage={clearImage}
            inputRef={inputRef}
            apiBase={API}
          />
        </div>
      )}
    </>
  );
};

export default RiseDualGPTChat;
