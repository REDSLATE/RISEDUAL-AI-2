import React, { useState, useCallback, useEffect, useRef } from 'react';
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

// LocalStorage key for the user-dragged chat-window position.
const POS_KEY = 'risedual:chat-pos';

/** Read a previously saved position (mobile and desktop each have their own). */
function loadPos() {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw);
    return typeof p?.x === 'number' && typeof p?.y === 'number' ? p : null;
  } catch {
    return null;
  }
}

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

  // Floating window drag state. `null` = use default docked position (bottom-right).
  // Once dragged, we pin `position: fixed; top:y; left:x` and ignore the Tailwind
  // bottom/right defaults so the user's last-dragged location sticks.
  const [pos, setPos] = useState(() => loadPos());
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef({ dx: 0, dy: 0, w: 0, h: 0 });
  const panelRef = useRef(null);

  const onHeaderPointerDown = useCallback((e) => {
    // Only drag from the header's non-button surface (buttons stopPropagation themselves).
    if (e.target.closest('button')) return;
    const panel = panelRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    dragRef.current = {
      dx: e.clientX - rect.left,
      dy: e.clientY - rect.top,
      w: rect.width,
      h: rect.height,
    };
    setDragging(true);
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch {}
  }, []);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e) => {
      const { dx, dy, w, h } = dragRef.current;
      // Clamp inside viewport with 4px padding so the window never gets lost.
      const pad = 4;
      const x = Math.max(pad, Math.min(window.innerWidth - w - pad, e.clientX - dx));
      const y = Math.max(pad, Math.min(window.innerHeight - h - pad, e.clientY - dy));
      setPos({ x, y });
    };
    const onUp = () => {
      setDragging(false);
      try {
        const p = {
          x: dragRef.current ? panelRef.current?.getBoundingClientRect().left : 0,
          y: dragRef.current ? panelRef.current?.getBoundingClientRect().top : 0,
        };
        if (isFinite(p.x) && isFinite(p.y)) {
          localStorage.setItem(POS_KEY, JSON.stringify(p));
        }
      } catch { /* quota/private mode — non-critical */ }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [dragging]);

  // Reset button — lets the user "snap back" to default corner position.
  const resetPosition = useCallback(() => {
    setPos(null);
    try { localStorage.removeItem(POS_KEY); } catch {}
  }, []);

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
        <div
          ref={panelRef}
          className={`fixed z-[60] w-[min(92vw,400px)] h-[72dvh] max-h-[620px] lg:h-[560px] lg:max-h-[calc(100vh-7rem)] flex flex-col bg-[#060E1F]/95 backdrop-blur-md rounded-2xl border border-slate-400/30 shadow-[0_20px_60px_-15px_rgba(0,0,0,0.75)] overflow-hidden ${
            dragging ? 'transition-none cursor-grabbing select-none' : 'transition-[top,left,bottom,right] duration-200'
          }`}
          style={pos
            ? { top: `${pos.y}px`, left: `${pos.x}px`, right: 'auto', bottom: 'auto' }
            : { bottom: '64px', right: '12px', left: 'auto', top: 'auto' }
          }
          data-testid="risedual-gpt-chat"
        >
          <div
            onPointerDown={onHeaderPointerDown}
            onDoubleClick={resetPosition}
            className="touch-none cursor-grab active:cursor-grabbing"
            title="Drag to move · double-tap to snap back"
            data-testid="chat-drag-handle"
          >
            <ChatHeader
              isPro={isPro} memoryEnabled={memoryEnabled} selectedImage={selectedImage}
              voiceMode={voiceMode} setVoiceMode={setVoiceMode} isSpeaking={isSpeaking} stopSpeaking={stopSpeaking}
              showMemory={showMemory} onToggleMemory={() => setShowMemory(!showMemory)} onLoadMemories={loadMemories}
              showPatterns={showPatterns} onTogglePatterns={() => setShowPatterns(!showPatterns)}
              showHistory={showHistory} onToggleHistory={() => setShowHistory(!showHistory)} onLoadHistory={loadHistory}
              onNewChat={handleNewChat} onClose={() => setIsOpen(false)}
            />
          </div>

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
