import { useState, useCallback, useRef } from 'react';
import { authFetch } from '../contexts/AuthContext';
import useChatMemory from './useChatMemory';
import useTTS from './useTTS';
import useStreamingAgent from './useStreamingAgent';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

// Pattern to detect messages that should use the streaming tools agent
const TOOLS_PATTERN = /\b(compound|cagr|future value|growth rate|invest(ment|ing)?.*worth|calculate|projection|project(ed)?|annualized|what would.*be worth|how much.*in \d+ years|rate of return|roi\b|current (stock )?price.*then|find.*price.*calculate|look up.*price.*and|worth in \d+)/i;

/**
 * Core chat state + side-effects hook for RiseDualGPTChat.
 *
 * Encapsulates:
 *   - Message list + input + loading + session id state
 *   - Image upload staging
 *   - `sendMessage`: streaming-agent detection, standard chat fallback,
 *     follow-up chip fetch, TTS playback
 *   - Session CRUD: `loadHistory`, `loadSession`, `newChat`
 *   - Proxied integrations: TTS, streaming agent, memory
 *
 * The owner component (`RiseDualGPTChat`) keeps pure-view state (isOpen,
 * showHistory, showPatterns, copiedId, showMemory).
 *
 * @param {object} opts
 * @param {boolean} opts.isPro
 * @param {function} [opts.onLimitReached]
 */
export default function useChat({ isPro, onLimitReached } = {}) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(() => `s_${Date.now()}`);
  const [chatHistory, setChatHistory] = useState([]);
  const [selectedImage, setSelectedImage] = useState(null);
  const [imagePreview, setImagePreview] = useState(null);
  const inputRef = useRef(null);

  const tts = useTTS();
  const { agentTrace, sendStreamingAgent } = useStreamingAgent(sessionId);
  const memory = useChatMemory(isPro);
  const { voiceMode, playTTS } = tts;

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

  const handleSuggestionClick = useCallback((text) => {
    setInput(text);
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

      // HTTP 402 → the ai_service detected an LLM budget exhaustion
      // on the Emergent Universal Key and mapped it to a structured
      // payload. Render as an "actionable" assistant message so the
      // UI pill can render "Top up Universal Key" instead of a raw
      // "Chat request failed" error. The `error_code` field is what
      // ChatMessages.jsx keys off to swap renderers.
      if (res.status === 402) {
        const errData = await res.json().catch(() => ({}));
        const payload = errData.detail || errData || {};
        setMessages(prev => [...prev, {
          role: 'assistant',
          error_code: payload.error_code || 'llm_budget_exceeded',
          content: payload.message
            || 'The Universal Key LLM budget is exhausted. Top up at Profile → Universal Key → Add Balance.',
          action_url: payload.action_url,
          action_label: payload.action_label || 'Top up Universal Key',
        }]);
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
      }
    } catch (err) {
      logger.error('Error loading session:', err);
    }
  }, []);

  const newChat = useCallback(() => {
    setSessionId(`s_${Date.now()}`);
    setMessages([]);
  }, []);

  return {
    // State
    messages, setMessages,
    input, setInput,
    loading,
    sessionId,
    chatHistory,
    selectedImage, imagePreview,
    inputRef,
    // Actions
    sendMessage,
    loadHistory, loadSession, newChat,
    handleImageSelect, clearImage, handleSuggestionClick,
    // Proxied integrations (use them directly from here — avoids re-importing
    // in RiseDualGPTChat and keeps hook ordering consistent).
    tts,
    memory,
    agentTrace,
    API,
  };
}
