import React, { useState, useRef, useCallback, useEffect } from 'react';
import { MessageSquare, History, X, Plus, Trash2, Minimize2 } from 'lucide-react';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { ChatMessages, ChatInputArea } from './chat/ChatComponents';
import ChartPatternLibrary from './ChartPatternLibrary';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

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
      console.error('Error loading chat history:', err);
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
      console.error('Error loading session:', err);
    }
  }, []);

  const newChat = useCallback(() => {
    setSessionId(`s_${Date.now()}`);
    setMessages([]);
    setShowHistory(false);
  }, []);

  return (
    <>
      {/* Floating trigger button */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="fixed bottom-5 right-5 z-40 w-14 h-14 rounded-full bg-[#0052FF] hover:bg-[#2563EB] text-white shadow-lg shadow-[#0052FF]/30 flex items-center justify-center transition-all hover:scale-105"
          data-testid="chat-fab"
        >
          <MessageSquare className="w-6 h-6" />
        </button>
      )}

      {/* Chat panel */}
      {isOpen && (
        <div className="fixed bottom-4 right-4 z-50 w-[420px] max-w-[calc(100vw-2rem)] h-[600px] max-h-[calc(100vh-6rem)] flex flex-col bg-[#0F172A] rounded-2xl border border-slate-700/50 shadow-2xl shadow-black/40 overflow-hidden" data-testid="trade-gpt-chat">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-slate-700/50 flex-shrink-0">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-[#0052FF]/20 rounded-lg flex items-center justify-center">
                <MessageSquare className="w-4 h-4 text-[#0052FF]" />
              </div>
              <div>
                <h3 className="text-white text-sm font-semibold">RISEDUAL AI Chat</h3>
                <p className="text-slate-500 text-[10px]">
                  {isPro ? 'Pro — Unlimited' : 'Free — 5/day'}
                  {selectedImage && ' | Image attached'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-8" onClick={() => setShowPatterns(!showPatterns)} data-testid="patterns-toggle">
                Patterns
              </Button>
              <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-8" onClick={() => { setShowHistory(!showHistory); if (!showHistory) loadHistory(); }} data-testid="history-toggle">
                <History className="w-4 h-4" />
              </Button>
              <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-8" onClick={newChat} data-testid="new-chat">
                <Plus className="w-4 h-4" />
              </Button>
              <Button size="sm" variant="ghost" className="text-slate-400 hover:text-white h-8" onClick={() => setIsOpen(false)} data-testid="chat-close">
                <Minimize2 className="w-4 h-4" />
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
            <div className="border-b border-slate-700/50 max-h-[300px] overflow-y-auto flex-shrink-0">
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
  <div className="border-b border-slate-700/50 bg-slate-900/50 p-3 max-h-[250px] overflow-y-auto" data-testid="chat-history-sidebar">
    <div className="flex items-center justify-between mb-2">
      <span className="text-slate-400 text-xs font-medium">Chat History</span>
      <button onClick={onClose} className="text-slate-500 hover:text-white"><X className="w-3 h-3" /></button>
    </div>
    {(!history || history.length === 0) ? (
      <p className="text-slate-500 text-xs">No previous chats</p>
    ) : (
      <div className="space-y-1">
        {history.map((s) => (
          <button
            key={s.session_id}
            onClick={() => onSelect(s.session_id)}
            className="w-full text-left px-3 py-2 rounded-lg hover:bg-slate-800 transition-colors group"
            data-testid={`session-${s.session_id}`}
          >
            <div className="text-white text-xs font-medium truncate">{s.preview || 'Chat Session'}</div>
            <div className="text-slate-500 text-[10px]">{s.message_count || 0} messages</div>
          </button>
        ))}
      </div>
    )}
  </div>
);

export default TradeGPTChat;
