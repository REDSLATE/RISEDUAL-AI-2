import React, { useState, useCallback } from 'react';
import {
  Activity, Brain, History, MessageSquare, Plus, RefreshCw,
  Sparkles, Terminal, TrendingUp,
} from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { ChatMessages, ChatInputArea } from '../chat/ChatComponents';
import ChatHistorySidebar from '../chat/ChatHistorySidebar';
import MemoryPanel from '../chat/MemoryPanel';
import AgentTrace from '../chat/AgentTrace';
import MCCard from '../chat/MCCard';
import useChat from '../../hooks/useChat';
import useMCContext from '../../hooks/useMCContext';

/**
 * AI Assistant Hub — the 3-pane MC-aware workspace.
 *
 *   LEFT (260px)   : Context / Council / Honesty Mirror snapshot
 *   CENTER (flex)  : RISEDUAL Chat (full-height, slash-command capable)
 *   RIGHT (300px)  : AI Recommendations / Recent Intent receipts
 *
 * Doctrine:
 *   - Chat asks, displays, opines.
 *   - Chat never executes — MC remains authority.
 *   - Owner-only data (sidecar / mirror / intents) is gated by JWT;
 *     non-owners see a friendly placeholder, not an error wall.
 */
const AIAssistantHub = ({ onSubscribe }) => {
  const { isPro } = useAuth();
  const chat = useChat({ isPro, onLimitReached: onSubscribe });
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
    memory: { memories, memoryEnabled, loadMemories, toggleMemory, deleteMemoryItem, clearAllMemories, pinToMemory },
    agentTrace,
    API,
  } = chat;

  const mc = useMCContext({ intervalMs: 30_000 });

  const [showHistory, setShowHistory] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [copiedId, setCopiedId] = useState(null);

  const handleCopy = useCallback((idx, text) => {
    navigator.clipboard.writeText(text);
    setCopiedId(idx);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  const runSlash = useCallback((cmd) => {
    setInput(cmd);
    setTimeout(() => sendMessage(), 0);
  }, [setInput, sendMessage]);

  const handleLoadSession = useCallback(async (sid) => {
    await loadSession(sid);
    setShowHistory(false);
  }, [loadSession]);

  const QUICK_PROMPTS = [
    { label: '/mc status', testid: 'qp-mc-status' },
    { label: '/mc mirror', testid: 'qp-mc-mirror' },
    { label: '/mc intents', testid: 'qp-mc-intents' },
    { label: '/mc help', testid: 'qp-mc-help' },
  ];

  return (
    <div
      className="w-full grid gap-3"
      style={{ gridTemplateColumns: 'minmax(0, 260px) minmax(0, 1fr) minmax(0, 300px)' }}
      data-testid="ai-assistant-hub"
    >
      {/* ── LEFT PANE · Context ─────────────────────────────────── */}
      <aside className="hidden lg:flex flex-col gap-3 bg-slate-900/40 border border-slate-700/50 rounded-xl p-3 max-h-[calc(100vh-9rem)] overflow-y-auto" data-testid="ai-context-panel">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Brain className="w-3.5 h-3.5 text-[#3DE8D9]" />
            <h2 className="text-white text-[12px] font-semibold tracking-wide uppercase">Context</h2>
          </div>
          <button
            onClick={mc.refresh}
            className="text-slate-500 hover:text-[#3DE8D9] transition-colors"
            title="Refresh MC context"
            data-testid="mc-context-refresh"
          >
            <RefreshCw className={`w-3 h-3 ${mc.loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {mc.loading && !mc.lastUpdated && (
          <div className="text-[11px] text-slate-500 py-2">Loading MC state…</div>
        )}

        {!mc.loading && mc.ownerScope === false && (
          <div className="text-[11px] text-slate-400 bg-slate-800/50 border border-slate-700/50 rounded-md p-2.5 leading-snug">
            <p>The Context pane mirrors live Mission Control state.</p>
            <p className="mt-1.5 text-slate-500">Operator-only — sign in as the owner to see sidecar, honesty mirror, and recent intents.</p>
          </div>
        )}

        {mc.sidecar && (
          <MCCard card={{ kind: 'mc_status', ...mc.sidecar, running: mc.sidecar.status === 'running', heartbeat_alive: mc.sidecar.heartbeat_task_alive, contribution_alive: mc.sidecar.contribution_task_alive, watchdog_alive: mc.sidecar.watchdog_task_alive, liveness_age_s: (mc.sidecar.watchdog || {}).liveness_age_s }} />
        )}

        {mc.mirror && (
          <MCCard card={{ kind: 'mc_mirror', ...mc.mirror }} />
        )}

        {mc.lastUpdated && (
          <div className="mt-auto text-[10px] text-slate-500 font-mono">
            updated {mc.lastUpdated.toLocaleTimeString()}
          </div>
        )}
      </aside>

      {/* ── CENTER PANE · Chat ──────────────────────────────────── */}
      <section className="flex flex-col bg-slate-900/60 border border-slate-700/50 rounded-xl overflow-hidden max-h-[calc(100vh-9rem)]" data-testid="ai-chat-panel">
        <header className="flex items-center justify-between px-3 py-2 border-b border-slate-700/50 flex-shrink-0">
          <div className="flex items-center gap-2">
            <img src="/logo-ai-bright2.png" alt="AI" className="w-7 h-7 object-contain" />
            <div>
              <h3 className="text-white text-[13px] font-semibold leading-tight" data-testid="ai-assistant-title">
                AI Assistant
              </h3>
              <p className="text-slate-400 text-[10px] leading-tight">
                MC-aware · {isPro ? 'Pro · Unlimited' : 'Free · 5/day'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {isPro && (
              <button
                onClick={() => { setShowMemory(!showMemory); if (!showMemory) loadMemories(); }}
                className={`p-1.5 rounded hover:bg-slate-800 transition-colors ${memoryEnabled ? 'text-[#3DE8D9]' : 'text-slate-400'}`}
                title="Memory"
                data-testid="ai-memory-btn"
              >
                <Brain className="w-3.5 h-3.5" />
              </button>
            )}
            <button
              onClick={() => { setShowHistory(!showHistory); if (!showHistory) loadHistory(); }}
              className="p-1.5 rounded hover:bg-slate-800 text-slate-400 transition-colors"
              title="History"
              data-testid="ai-history-btn"
            >
              <History className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={newChat}
              className="p-1.5 rounded hover:bg-slate-800 text-slate-400 transition-colors"
              title="New chat"
              data-testid="ai-new-chat-btn"
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
          </div>
        </header>

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

        <AgentTrace trace={agentTrace} />

        <ChatMessages
          messages={messages}
          showPatterns={false}
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
      </section>

      {/* ── RIGHT PANE · Recommendations ────────────────────────── */}
      <aside className="hidden lg:flex flex-col gap-3 bg-slate-900/40 border border-slate-700/50 rounded-xl p-3 max-h-[calc(100vh-9rem)] overflow-y-auto" data-testid="ai-recs-panel">
        <div className="flex items-center gap-1.5">
          <Sparkles className="w-3.5 h-3.5 text-[#3DE8D9]" />
          <h2 className="text-white text-[12px] font-semibold tracking-wide uppercase">AI Recommendations</h2>
        </div>

        {/* Quick prompts — always visible */}
        <div>
          <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">Quick commands</p>
          <div className="grid grid-cols-2 gap-1.5">
            {QUICK_PROMPTS.map(q => (
              <button
                key={q.label}
                onClick={() => runSlash(q.label)}
                className="text-left text-[11px] font-mono text-[#3DE8D9] bg-slate-800/60 hover:bg-slate-800 border border-slate-700/50 hover:border-[#3DE8D9]/40 rounded px-2 py-1.5 transition-colors"
                data-testid={q.testid}
              >
                {q.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => setInput('/mc opine ')}
            className="mt-1.5 w-full text-left text-[11px] font-mono text-[#3DE8D9] bg-slate-800/60 hover:bg-slate-800 border border-slate-700/50 hover:border-[#3DE8D9]/40 rounded px-2 py-1.5 transition-colors"
            data-testid="qp-mc-opine"
          >
            /mc opine &lt;ticker&gt;
          </button>
        </div>

        {/* Live intents — owner only */}
        {mc.ownerScope && mc.intents && (
          <div>
            <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">Recent intents</p>
            <MCCard card={{ kind: 'mc_intents', ...mc.intents }} />
          </div>
        )}

        {!mc.ownerScope && (
          <div className="text-[11px] text-slate-400 bg-slate-800/50 border border-slate-700/50 rounded-md p-2.5 leading-snug">
            <p className="font-semibold text-slate-300 mb-1">Want live recommendations?</p>
            <p>Slash commands above work for everyone. Live MC receipts (recent intents, council overrides) are operator-only.</p>
          </div>
        )}

        <div className="mt-auto text-[10px] text-slate-500 leading-snug">
          <p className="flex items-center gap-1"><Terminal className="w-3 h-3" /> Read-only surface</p>
          <p className="mt-0.5">Chat asks &amp; displays. MC remains execution authority.</p>
        </div>
      </aside>
    </div>
  );
};

export default AIAssistantHub;
