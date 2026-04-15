import React, { useState, useRef, useCallback, useEffect } from 'react';
import { MessageSquare } from 'lucide-react';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { ChatMessages, ChatInputArea } from './chat/ChatComponents';
import ChatHeader from './chat/ChatHeader';
import MemoryPanel from './chat/MemoryPanel';
import ChatHistorySidebar from './chat/ChatHistorySidebar';
import ChartPatternLibrary from './ChartPatternLibrary';
import useChatMemory from '../hooks/useChatMemory';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const VOICE_MAP = { female: 'nova', male: 'onyx' };

// Pattern to detect messages that should use the streaming tools agent
const TOOLS_PATTERN = /\b(compound|cagr|future value|growth rate|invest(ment|ing)?.*worth|calculate|projection|project(ed)?|annualized|what would.*be worth|how much.*in \d+ years|rate of return|roi\b|current (stock )?price.*then|find.*price.*calculate|look up.*price.*and|worth in \d+)/i;

const TOOL_LABELS = {
  get_stock_quote: 'Looking up price',
  web_search: 'Searching the web',
  calculate_compound_growth: 'Calculating growth',
  calculate_cagr: 'Computing CAGR',
  get_daily_history: 'Fetching price history',
};

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
  const [voiceMode, setVoiceMode] = useState(null);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [agentTrace, setAgentTrace] = useState(null);
  const { memories, memoryEnabled, loadMemories, toggleMemory, deleteMemoryItem, clearAllMemories, pinToMemory } = useChatMemory(isPro);
  const audioRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    const handler = () => setIsOpen(true);
    window.addEventListener('risedualai-open-chat', handler);
    return () => window.removeEventListener('risedualai-open-chat', handler);
  }, []);

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

  const playTTS = useCallback(async (text) => {
    if (!voiceMode) return;
    try {
      setIsSpeaking(true);
      const clean = text.replace(/[#*_`\[\]()>~|]/g, '').replace(/\n{2,}/g, '. ').substring(0, 4096);
      const res = await authFetch(`${API}/chat/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: clean, voice: VOICE_MAP[voiceMode] }),
      });
      if (!res.ok) throw new Error('TTS failed');
      const data = await res.json();
      const audio = new Audio(`data:audio/mp3;base64,${data.audio}`);
      audioRef.current = audio;
      audio.onended = () => setIsSpeaking(false);
      audio.onerror = () => setIsSpeaking(false);
      await audio.play();
    } catch (err) {
      logger.error('TTS error:', err);
      setIsSpeaking(false);
    }
  }, [voiceMode]);

  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setIsSpeaking(false);
  }, []);

  // SSE streaming agent for tool-heavy queries
  const sendStreamingAgent = useCallback(async (text) => {
    setAgentTrace([]);
    const url = new URL(`${API}/chat/agent-stream`, window.location.origin);
    url.searchParams.set('message', text);
    url.searchParams.set('sessionId', sessionId);

    return new Promise((resolve, reject) => {
      const es = new EventSource(url.toString());
      let finalText = '';
      let providerInfo = null;
      let toolsUsed = [];

      es.addEventListener('agent', (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.action === 'tool_calls' && data.tools) {
            setAgentTrace(prev => [...(prev || []),
              ...data.tools.map(t => ({ type: 'calling', tool: t, label: TOOL_LABELS[t] || t }))
            ]);
          }
          if (data.action === 'final_answer') {
            finalText = data.text || '';
            providerInfo = data.provider || null;
          }
        } catch {}
      });

      es.addEventListener('tools', (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.tool) {
            toolsUsed.push(data.tool);
            setAgentTrace(prev => {
              const updated = [...(prev || [])];
              const idx = updated.findIndex(t => t.tool === data.tool && t.type === 'calling');
              if (idx >= 0) {
                updated[idx] = { type: 'done', tool: data.tool, label: TOOL_LABELS[data.tool] || data.tool, result: data.result };
              } else {
                updated.push({ type: 'done', tool: data.tool, label: TOOL_LABELS[data.tool] || data.tool, result: data.result });
              }
              return updated;
            });
          }
        } catch {}
      });

      es.addEventListener('end', (e) => {
        try {
          const data = JSON.parse(e.data);
          providerInfo = data.provider || providerInfo;
          toolsUsed = data.tools_used || toolsUsed;
        } catch {}
        es.close();
        setAgentTrace(null);
        resolve({ text: finalText, provider: providerInfo, tools_used: [...new Set(toolsUsed)] });
      });

      es.addEventListener('error', (e) => {
        try {
          const data = JSON.parse(e.data);
          es.close();
          setAgentTrace(null);
          reject(new Error(data.error || 'Agent stream failed'));
        } catch {
          es.close();
          setAgentTrace(null);
          reject(new Error('Agent stream connection failed'));
        }
      });

      es.onerror = () => {
        es.close();
        setAgentTrace(null);
        // If we got a final answer before the connection closed, resolve with it
        if (finalText) {
          resolve({ text: finalText, provider: providerInfo, tools_used: [...new Set(toolsUsed)] });
        } else {
          reject(new Error('Agent stream connection lost'));
        }
      };
    });
  }, [sessionId]);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text && !selectedImage) return;

    const userMessage = { role: 'user', content: text || 'Analyze this chart image', image: imagePreview };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);

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
      const providerInfo = data.provider;
      const toolsUsed = data.tools_used;
      setMessages(prev => [...prev, { role: 'assistant', content: aiText, provider: providerInfo, tools_used: toolsUsed }]);
      if (voiceMode) playTTS(aiText);
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

          {/* Agent trace — live tool execution */}
          {agentTrace && agentTrace.length > 0 && (
            <div className="px-3 py-2 border-b border-slate-700/40 bg-slate-900/60 flex-shrink-0" data-testid="agent-trace">
              <div className="flex items-center gap-1.5 mb-1.5">
                <div className="w-1.5 h-1.5 rounded-full bg-[#3DE8D9] animate-pulse" />
                <span className="text-[10px] text-[#3DE8D9] font-semibold uppercase tracking-wider">Agent Working</span>
              </div>
              <div className="space-y-1">
                {agentTrace.map((t, i) => (
                  <div key={`${t.type}-${t.label}-${i}`} className="flex items-center gap-2 text-[11px]">
                    {t.type === 'calling' ? (
                      <>
                        <div className="w-3 h-3 border border-[#3DE8D9]/40 border-t-[#3DE8D9] rounded-full animate-spin" />
                        <span className="text-slate-400">{t.label}...</span>
                      </>
                    ) : (
                      <>
                        <svg className="w-3 h-3 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                        <span className="text-slate-300">{t.label}</span>
                        {t.result?.price && <span className="text-[#3DE8D9] font-mono">${t.result.price}</span>}
                        {t.result?.future_value && <span className="text-[#3DE8D9] font-mono">${t.result.future_value.toLocaleString()}</span>}
                        {t.result?.cagr_pct && <span className="text-[#3DE8D9] font-mono">{t.result.cagr_pct}%</span>}
                        {t.result?.historical_cagr_pct && <span className="text-[#3DE8D9] font-mono">{t.result.historical_cagr_pct}% CAGR</span>}
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <ChatMessages
            messages={messages}
            showPatterns={showPatterns}
            copiedId={copiedId}
            onCopy={handleCopy}
            isPro={isPro}
            onPin={(content) => pinToMemory(content, setMessages)}
            onSuggestionClick={handleSuggestionClick}
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
