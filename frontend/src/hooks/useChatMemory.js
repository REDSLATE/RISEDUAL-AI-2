import { useState, useCallback } from 'react';
import { authFetch } from '../contexts/AuthContext';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Custom hook for RiseDualGPT persistent memory — load, toggle, pin, delete.
 * Note: API, authFetch, logger are module-level constants — stable across renders.
 */
export default function useChatMemory(isPro) {
  const [memories, setMemories] = useState([]);
  const [memoryEnabled, setMemoryEnabled] = useState(true);

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
  }, [isPro]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleMemory = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/chat/memory/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !memoryEnabled }),
      });
      if (res.ok) setMemoryEnabled(!memoryEnabled);
    } catch (e) { logger.error('Memory toggle error:', e); }
  }, [memoryEnabled]); // eslint-disable-line react-hooks/exhaustive-deps

  const deleteMemoryItem = useCallback(async (memoryId) => {
    try {
      const res = await authFetch(`${API}/chat/memory/${memoryId}`, { method: 'DELETE' });
      if (res.ok) setMemories(prev => prev.filter(m => m.memory_id !== memoryId));
    } catch (e) { logger.error('Memory delete error:', e); }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const clearAllMemories = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/chat/memory`, { method: 'DELETE' });
      if (res.ok) setMemories([]);
    } catch (e) { logger.error('Memory clear error:', e); }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const pinToMemory = useCallback(async (content, setMessages) => {
    if (!isPro) return;
    try {
      const summary = content.length > 500 ? content.substring(0, 500) : content;
      const res = await authFetch(`${API}/chat/memory/pin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: summary }),
      });
      if (res.ok) {
        loadMemories();
        return await res.json();
      } else {
        const err = await res.json().catch(() => ({}));
        if (err.detail && setMessages) {
          setMessages(prev => [...prev, { role: 'assistant', content: `Memory pin failed: ${err.detail}` }]);
        }
      }
    } catch (e) { logger.error('Pin error:', e); }
  }, [isPro, loadMemories]); // eslint-disable-line react-hooks/exhaustive-deps

  return { memories, memoryEnabled, loadMemories, toggleMemory, deleteMemoryItem, clearAllMemories, pinToMemory };
}
