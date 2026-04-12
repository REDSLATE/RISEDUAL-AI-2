import React, { useState, useRef, useCallback, useEffect } from 'react';
import { MessageSquare } from 'lucide-react';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { ChatMessages, ChatInputArea } from './chat/ChatComponents';
import ChatHeader from './chat/ChatHeader';
import MemoryPanel from './chat/MemoryPanel';
import ChartPatternLibrary from './ChartPatternLibrary';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const VOICE_MAP = { female: 'nova', male: 'onyx' };

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
  const [memories, setMemories] = useState([]);
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const audioRef = useRef(null);
  const inputRef = useRef(null);

  // Listen for the global open-chat event from Navbar
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

  // TTS: play AI response aloud
  const playTTS = useCallback(async (text) => {
    if (!voiceMode) return;
    try {
      setIsSpeaking(true);
      // Strip markdown formatting for cleaner speech
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

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text && !selectedImage) return;

    const userMessage = { role: 'user', content: text || 'Analyze this chart image', image: imagePreview };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
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
      setMessages(prev => [...prev, { role: 'assistant', content: aiText }]);
      // Speak the response if voice is enabled
      if (voiceMode) playTTS(aiText);
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${err.message}. Please try again.` }]);
    } finally {
      setLoading(false);
    }
  }, [input, selectedImage, imagePreview, sessionId, clearImage, onLimitReached, voiceMode, playTTS]);

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

  // Memory functions
  const loadMemories = useCallback(async () => {
    if (!isPro) return;
    try {
      const res = await authFetch(`${API}/chat/memory`);
      if (res.ok) {
        const data = await res.json();
        setMemories(data.memories || []);
        setMemoryEnabled(data.enabled);
      }
    } catch (e) { logger.error('Memory load error:', e); }
  }, [isPro]);

  const toggleMemory = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/chat/memory/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !memoryEnabled }),
      });
      if (res.ok) setMemoryEnabled(!memoryEnabled);
    } catch (e) { logger.error('Memory toggle error:', e); }
  }, [memoryEnabled]);

  const deleteMemoryItem = useCallback(async (memoryId) => {
    try {
      const res = await authFetch(`${API}/chat/memory/${memoryId}`, { method: 'DELETE' });
      if (res.ok) setMemories(prev => prev.filter(m => m.memory_id !== memoryId));
    } catch (e) { logger.error('Memory delete error:', e); }
  }, []);

  const clearAllMemories = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/chat/memory`, { method: 'DELETE' });
      if (res.ok) setMemories([]);
    } catch (e) { logger.error('Memory clear error:', e); }
  }, []);

  const pinToMemory = useCallback(async (content) => {
    if (!isPro) return;
    try {
      const summary = content.length > 500 ? content.substring(0, 500) : content;
      const res = await authFetch(`${API}/chat/memory/pin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: summary }),
      });
      if (res.ok) {
        const data = await res.json();
        loadMemories();
        return data;
      } else {
        const err = await res.json().catch(() => ({}));
        if (err.detail) {
          setMessages(prev => [...prev, { role: 'assistant', content: `Memory pin failed: ${err.detail}` }]);
        }
      }
    } catch (e) { logger.error('Pin error:', e); }
  }, [isPro, loadMemories]);

  const handleSuggestionClick = useCallback((text) => {
    setInput(text);
    inputRef.current?.focus();
  }, []);

  return (
    <>
      {/* Floating trigger button — above mobile nav */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="fixed bottom-20 lg:bottom-5 right-5 z-[60] w-14 h-14 rounded-full bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white shadow-lg shadow-[#3DE8D9]/30 flex items-center justify-center transition-all hover:scale-105"
          data-testid="chat-fab"
        >
          <MessageSquare className="w-6 h-6" />
        </button>
      )}

      {/* Chat panel — floats above everything including mobile nav */}
      {isOpen && (
        <div className="fixed bottom-0 right-0 lg:bottom-4 lg:right-4 z-[60] w-full lg:w-[360px] lg:max-w-[calc(100vw-2rem)] h-[calc(100dvh-3.5rem)] lg:h-[480px] lg:max-h-[calc(100vh-6rem)] flex flex-col bg-[#060E1F] lg:rounded-2xl border-t lg:border border-slate-400/25 shadow-2xl shadow-black/40 overflow-hidden pb-safe" data-testid="risedual-gpt-chat">
          {/* Header */}
          <ChatHeader
            isPro={isPro} memoryEnabled={memoryEnabled} selectedImage={selectedImage}
            voiceMode={voiceMode} setVoiceMode={setVoiceMode} isSpeaking={isSpeaking} stopSpeaking={stopSpeaking}
            showMemory={showMemory} onToggleMemory={() => setShowMemory(!showMemory)} onLoadMemories={loadMemories}
            showPatterns={showPatterns} onTogglePatterns={() => setShowPatterns(!showPatterns)}
            showHistory={showHistory} onToggleHistory={() => setShowHistory(!showHistory)} onLoadHistory={loadHistory}
            onNewChat={newChat} onClose={() => setIsOpen(false)}
          />

          {/* Sidebar: History */}
          {showHistory && (
            <ChatHistorySidebar
              history={chatHistory}
              onSelect={loadSession}
              onClose={() => setShowHistory(false)}
            />
          )}

          {/* Memory Panel */}
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

          {/* Pattern Library */}
          {showPatterns && (
            <div className="border-b border-slate-400/25 max-h-[240px] overflow-y-auto flex-shrink-0">
              <ChartPatternLibrary onPatternSelect={handlePatternSelect} compact />
            </div>
          )}

          {/* Messages */}
          <ChatMessages
            messages={messages}
            showPatterns={showPatterns}
            copiedId={copiedId}
            onCopy={handleCopy}
            isPro={isPro}
            onPin={pinToMemory}
            onSuggestionClick={handleSuggestionClick}
          />

          {/* Input */}
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

const ChatHistorySidebar = ({ history, onSelect, onClose }) => (
  <div className="border-b border-slate-400/25 bg-slate-800/50 px-2.5 py-2 max-h-[200px] overflow-y-auto" data-testid="chat-history-sidebar">
    <div className="flex items-center justify-between mb-1.5">
      <span className="text-slate-400 text-[11px] font-medium">Chat History</span>
      <button onClick={onClose} className="text-slate-400 hover:text-white"><X className="w-3 h-3" /></button>
    </div>
    {(!history || history.length === 0) ? (
      <p className="text-slate-400 text-[11px]">No previous chats</p>
    ) : (
      <div className="space-y-0.5">
        {history.map((s) => (
          <button
            key={s.session_id}
            onClick={() => onSelect(s.session_id)}
            className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-slate-600/30 transition-colors group"
            data-testid={`session-${s.session_id}`}
          >
            <div className="text-white text-[11px] font-medium truncate">{s.preview || 'Chat Session'}</div>
            <div className="text-slate-400 text-[9px]">{s.message_count || 0} messages</div>
          </button>
        ))}
      </div>
    )}
  </div>
);

export default RiseDualGPTChat;
