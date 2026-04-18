import React, { useState, useRef, useCallback, useEffect } from 'react';
import { MessageSquare } from 'lucide-react';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { ChatMessages, ChatInputArea } from './chat/ChatComponents';
import ChatHeader from './chat/ChatHeader';
import MemoryPanel from './chat/MemoryPanel';
import ChatHistorySidebar from './chat/ChatHistorySidebar';
import AgentTrace from './chat/AgentTrace';
import ChartPatternLibrary from './ChartPatternLibrary';
import useChatMemory from '../hooks/useChatMemory';
import useTTS from '../hooks/useTTS';
import useStreamingAgent from '../hooks/useStreamingAgent';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

// Pattern to detect messages that should use the streaming tools agent
const TOOLS_PATTERN = /\b(compound|cagr|future value|growth rate|invest(ment|ing)?.*worth|calculate|projection|project(ed)?|annualized|what would.*be worth|how much.*in \d+ years|rate of return|roi\b|current (stock )?price.*then|find.*price.*calculate|look up.*price.*and|worth in \d+)/i;

const RiseDualGPTChat = ({ onLimitReached }) => {
  const { isPro } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(() => `s_${Date.now()}`);
  const [chatHistory, setChatHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [showPatterns, setShowPatterns] = useState(false);
  const [selectedImage, setSelectedImage] = useState(null);
  const [imagePreview, setImagePreview] = useState(null);
  const [copiedId, setCopiedId] = useState(null);
  const [showMemory, setShowMemory] = useState(false);

  const { voiceMode, setVoiceMode, isSpeaking, playTTS, stopSpeaking } = useTTS();
  const { agentTrace, sendStreamingAgent } = useStreamingAgent(sessionId);
  const { memories, memoryEnabled, loadMemories, toggleMemory, deleteMemoryItem, clearAllMemories, pinToMemory } = useChatMemory(isPro);
  const inputRef = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      setIsOpen(true);
      const detail = e?.detail;
      if (detail?.prefill) {
        setInput(detail.prefill);
        if (detail.autoSend) {
          // Defer so the input state settles and the chat mounts before send
          setTimeout(() => {
            window.dispatchEvent(new CustomEvent('risedualai-autosend'));
          }, 400);
        }
      }
    };
    window.addEventListener('risedualai-open-chat', handler);
    return () => window.removeEventListener('risedualai-open-chat', handler);
  }, []);

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

  const handleImageSelect = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setSelectedImage(file);
    const reader = new FileReader();
    reader.onload = (ev) => setImagePreview(ev.target.result);
    reader.readAsDataURL(file);
  }, []);

  const clearImage = useCallback(() => {
    setSelectedImage(null);
    setImagePreview(null);
  }, []);

  const handlePatternSelect = useCallback((pattern) => {
    setInput(`Analyze ${pattern.name} chart pattern: When does it typically form? What's the expected breakout direction and target? Current success rate?`);
    setShowPatterns(false);
    inputRef.current?.focus();
  }, []);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text && !selectedImage) return;

    const userMessage = { role: 'user', content: text || 'Analyze this chart image', image: imagePreview };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    // Helper: fetch 3 contextual follow-up chips after assistant replies.
    // Non-blocking; failure is silent (chips are optional).
    const fetchFollowups = (userMsgText, assistantText) => {
      if (!assistantText || assistantText.length < 20) return;
      fetch(`${API}/chat/followups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          last_user_message: userMsgText,
          last_assistant_response: assistantText,
          context_hub: typeof window !== 'undefined'
            ? (window.__risedualActiveView || null)
            : null,
        }),
      })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (!data) return;
          const chips = Array.isArray(data.chips) && data.chips.length ? data.chips : null;
          const actions = Array.isArray(data.actions) ? data.actions.filter(a => a && a.label && a.kind) : [];
          if (!chips && !actions.length) return;
          // Attach chips + actions to the most recent assistant message.
          setMessages(prev => {
            const idx = [...prev].reverse().findIndex(m => m.role === 'assistant');
            if (idx < 0) return prev;
            const realIdx = prev.length - 1 - idx;
            const copy = [...prev];
            copy[realIdx] = {
              ...copy[realIdx],
              ...(chips ? { followups: chips } : {}),
              ...(actions.length ? { actions } : {}),
            };
            return copy;
          });
        })
        .catch(() => { /* silent */ });
    };

    try {
      // Use streaming agent for calculation-heavy queries (no image)
      const useAgent = !selectedImage && TOOLS_PATTERN.test(text);

      if (useAgent) {
        try {
          const result = await sendStreamingAgent(text);
          if (result.text) {
            setMessages(prev => [...prev, {
              role: 'assistant',
              content: result.text,
              provider: result.provider,
              tools_used: result.tools_used,
            }]);
            if (voiceMode) playTTS(result.text);
            fetchFollowups(text, result.text);
            return;
          }
        } catch (streamErr) {
          logger.warn('Streaming agent failed, falling back to standard chat:', streamErr);
          // Fall through to standard chat
        }
      }

      // Standard chat path
      const body = new FormData();
      body.append('message', text || 'Analyze this chart image');
      body.append('sessionId', sessionId);
      if (selectedImage) body.append('image', selectedImage);

      const res = await authFetch(`${API}/chat`, { method: 'POST', body });
      clearImage();

      if (res.status === 429) {
        const errData = await res.json().catch(() => ({}));
        setMessages(prev => [...prev, { role: 'assistant', content: errData.detail || 'Daily free limit reached. Upgrade to Pro for unlimited access.' }]);
        if (onLimitReached) onLimitReached();
        return;
      }

      if (!res.ok) throw new Error('Chat request failed');
      const data = await res.json();
      const aiText = data.response || data.message || 'No response generated.';
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: aiText,
        provider: data.provider,
        tools_used: data.tools_used,
      }]);
      if (voiceMode) playTTS(aiText);
      fetchFollowups(text, aiText);
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${err.message}. Please try again.` }]);
    } finally {
      setLoading(false);
      clearImage();
    }
  }, [input, selectedImage, imagePreview, sessionId, clearImage, onLimitReached, voiceMode, playTTS, sendStreamingAgent]);

  const loadHistory = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/chat/sessions`);
      if (res.ok) setChatHistory(await res.json());
    } catch (err) {
      logger.error('Error loading chat history:', err);
    }
  }, []);

  const loadSession = useCallback(async (sid) => {
    try {
      const res = await authFetch(`${API}/chat/history/${sid}`);
      if (res.ok) {
        const data = await res.json();
        setSessionId(sid);
        setMessages(data.messages || []);
        setShowHistory(false);
      }
    } catch (err) {
      logger.error('Error loading session:', err);
    }
  }, []);

  const newChat = useCallback(() => {
    setSessionId(`s_${Date.now()}`);
    setMessages([]);
    setShowHistory(false);
  }, []);

  const handleSuggestionClick = useCallback((text) => {
    setInput(text);
    inputRef.current?.focus();
  }, []);

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
        <div className="fixed bottom-0 right-0 lg:bottom-4 lg:right-4 z-[60] w-full lg:w-[360px] lg:max-w-[calc(100vw-2rem)] h-[calc(100dvh-3.5rem)] lg:h-[480px] lg:max-h-[calc(100vh-6rem)] flex flex-col bg-[#060E1F] lg:rounded-2xl border-t lg:border border-slate-400/25 shadow-2xl shadow-black/40 overflow-hidden pb-safe" data-testid="risedual-gpt-chat">
          <ChatHeader
            isPro={isPro} memoryEnabled={memoryEnabled} selectedImage={selectedImage}
            voiceMode={voiceMode} setVoiceMode={setVoiceMode} isSpeaking={isSpeaking} stopSpeaking={stopSpeaking}
            showMemory={showMemory} onToggleMemory={() => setShowMemory(!showMemory)} onLoadMemories={loadMemories}
            showPatterns={showPatterns} onTogglePatterns={() => setShowPatterns(!showPatterns)}
            showHistory={showHistory} onToggleHistory={() => setShowHistory(!showHistory)} onLoadHistory={loadHistory}
            onNewChat={newChat} onClose={() => setIsOpen(false)}
          />

          {showHistory && (
            <ChatHistorySidebar
              history={chatHistory}
              onSelect={loadSession}
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
            onActionClick={(act, msgIdx) => {
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

              // Dispatch the right navigation + payload event.
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
              // Close chat so the target surface is visible
              setIsOpen(false);
            }}
            onFollowupClick={(chip, msgIdx) => {
              // Fire-and-forget adoption telemetry — non-blocking, silent on failure.
              try {
                fetch(`${API}/analytics/chip-event`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  credentials: 'include',
                  body: JSON.stringify({
                    action: 'clicked',
                    chip_text: chip,
                    message_idx: typeof msgIdx === 'number' ? msgIdx : null,
                    context_hub: typeof window !== 'undefined'
                      ? (window.__risedualActiveView || null)
                      : null,
                  }),
                }).catch(() => { /* silent */ });
              } catch { /* silent */ }
              setInput(chip);
              // Defer one tick so setInput lands before sendMessage reads it
              setTimeout(() => sendMessage(), 0);
            }}
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
