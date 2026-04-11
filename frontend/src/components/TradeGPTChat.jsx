import React, { useState, useRef, useCallback, useEffect } from 'react';
import { MessageSquare, History, X, Plus, Minimize2 } from 'lucide-react';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { ChatMessages, ChatInputArea } from './chat/ChatComponents';
import ChartPatternLibrary from './ChartPatternLibrary';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const TradeGPTChat = ({ onLimitReached }) => {
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
      setMessages(prev => [...prev, { role: 'assistant', content: data.response || data.message || 'No response generated.' }]);
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${err.message}. Please try again.` }]);
    } finally {
      setLoading(false);
    }
  }, [input, selectedImage, imagePreview, sessionId, clearImage, onLimitReached]);

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
        <div className="fixed bottom-0 right-0 lg:bottom-4 lg:right-4 z-[60] w-full lg:w-[360px] lg:max-w-[calc(100vw-2rem)] h-[calc(100dvh-3.5rem)] lg:h-[480px] lg:max-h-[calc(100vh-6rem)] flex flex-col bg-[#060E1F] lg:rounded-2xl border-t lg:border border-slate-400/25 shadow-2xl shadow-black/40 overflow-hidden pb-safe" data-testid="trade-gpt-chat">
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-2 border-b border-slate-400/25 flex-shrink-0">
            <div className="flex items-center gap-2">
              <img src="/logo-ai-bright2.png" alt="RISEDUAL AI" className="w-7 h-7 object-contain" />
              <div>
                <h3 className="text-white text-xs font-semibold leading-tight">RISEDUAL AI</h3>
                <p className="text-slate-400 text-[9px] leading-tight">
                  {isPro ? 'Pro — Unlimited' : 'Free — 5/day'}
                  {selectedImage && ' · Image'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-0.5">
              <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-7 px-2 text-[11px]" onClick={() => setShowPatterns(!showPatterns)} data-testid="patterns-toggle">
                Patterns
              </Button>
              <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-7 w-7 p-0" onClick={() => { setShowHistory(!showHistory); if (!showHistory) loadHistory(); }} data-testid="history-toggle">
                <History className="w-3.5 h-3.5" />
              </Button>
              <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-7 w-7 p-0" onClick={newChat} data-testid="new-chat">
                <Plus className="w-3.5 h-3.5" />
              </Button>
              <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-7 w-7 p-0" onClick={() => setIsOpen(false)} data-testid="chat-close">
                <Minimize2 className="w-3.5 h-3.5" />
              </Button>
            </div>
          </div>

          {/* Sidebar: History */}
          {showHistory && (
            <ChatHistorySidebar
              history={chatHistory}
              onSelect={loadSession}
              onClose={() => setShowHistory(false)}
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
          />
        </div>
      )}
    </>
  );
};

const ChatHistorySidebar = ({ history, onSelect, onClose }) => (
  <div className="border-b border-slate-400/25 bg-slate-900/50 px-2.5 py-2 max-h-[200px] overflow-y-auto" data-testid="chat-history-sidebar">
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

export default TradeGPTChat;
