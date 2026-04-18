/**
 * useStreamingAgent — SSE-based streaming chat agent hook.
 *
 * Opens an EventSource to /api/chat/agent-stream, surfaces live tool-call
 * traces, and resolves to { text, provider, tools_used } when the stream ends.
 */
import { useState, useCallback } from 'react';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const TOOL_LABELS = {
  get_stock_quote: 'Looking up price',
  web_search: 'Searching the web',
  calculate_compound_growth: 'Calculating growth',
  calculate_cagr: 'Computing CAGR',
  get_daily_history: 'Fetching price history',
};

export default function useStreamingAgent(sessionId) {
  const [agentTrace, setAgentTrace] = useState(null);

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
        } catch { /* ignore malformed event */ }
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
        } catch { /* ignore malformed event */ }
      });

      es.addEventListener('end', (e) => {
        try {
          const data = JSON.parse(e.data);
          providerInfo = data.provider || providerInfo;
          toolsUsed = data.tools_used || toolsUsed;
        } catch { /* ignore malformed event */ }
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
        if (finalText) {
          resolve({ text: finalText, provider: providerInfo, tools_used: [...new Set(toolsUsed)] });
        } else {
          reject(new Error('Agent stream connection lost'));
        }
      };
    });
  }, [sessionId]);

  return { agentTrace, sendStreamingAgent };
}
