import React, { useRef, useEffect, useState } from 'react';
import { User, Copy, Check, Pin, Zap, ExternalLink } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { getApiBase } from '../../utils/apiBase';
import MCCard from './MCCard';

const CHIP_API = `${getApiBase()}/api/analytics/chip-event`;

// Single message bubble. Kept private to this file — the only consumer is
// ChatMessages.
const MessageBubble = ({ msg, idx, copiedId, onCopy, isPro, onPin }) => {
  const isUser = msg.role === 'user';
  const [pinned, setPinned] = useState(false);

  const handlePin = () => {
    if (onPin && !pinned) {
      onPin(msg.content);
      setPinned(true);
      setTimeout(() => setPinned(false), 3000);
    }
  };

  // MC slash-command card — when the user runs `/mc …`, useChat.js
  // attaches an `mc_card` payload instead of plain markdown content.
  // Render it as a compact assistant bubble with the structured card
  // inside, and skip the normal markdown / pin / copy chrome since
  // these are *system* read-outs, not LLM text.
  if (!isUser && msg.mc_card) {
    return (
      <div className="flex gap-2" data-testid={`message-mc-${idx}`}>
        <img src="/logo-ai-bright2.png" alt="AI" className="w-6 h-6 flex-shrink-0 object-contain mt-0.5" />
        <div className="max-w-[90%] flex-1">
          <MCCard card={msg.mc_card} />
        </div>
      </div>
    );
  }

  // LLM budget-exceeded banner — the backend returns HTTP 402 with
  // a structured payload when the Emergent Universal Key budget is
  // exhausted; `useChat.js` maps that into `msg.error_code` so we
  // can render an actionable pill instead of a raw error bubble.
  // Keeps the user in-flow ("ok, one click, I know what to do")
  // rather than staring at a stack trace.
  if (!isUser && msg.error_code === 'llm_budget_exceeded') {
    const href = msg.action_url || 'https://emergent.sh/profile/universal-key';
    return (
      <div className="flex gap-2" data-testid={`message-budget-${idx}`}>
        <img src="/logo-ai-bright2.png" alt="AI" className="w-6 h-6 flex-shrink-0 object-contain mt-0.5" />
        <div className="max-w-[82%] rounded-xl px-3.5 py-3 bg-amber-500/10 border border-amber-500/40 text-amber-100">
          <div className="flex items-start gap-2">
            <Zap className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-bold text-amber-300 mb-1">
                LLM budget exhausted
              </div>
              <p className="text-[12px] leading-snug text-amber-100/90">
                {msg.content}
              </p>
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 mt-2 text-[11px] font-semibold px-2.5 py-1 rounded-full bg-amber-400/20 text-amber-200 border border-amber-400/50 hover:bg-amber-400/30 hover:text-amber-100 transition-colors"
                data-testid={`budget-topup-link-${idx}`}
              >
                {msg.action_label || 'Top up Universal Key'}
                <ExternalLink className="w-2.5 h-2.5" />
              </a>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`flex gap-2 ${isUser ? 'justify-end' : ''}`} data-testid={`message-${idx}`}>
      {!isUser && (
        <img src="/logo-ai-bright2.png" alt="AI" className="w-6 h-6 flex-shrink-0 object-contain mt-0.5" />
      )}
      <div className={`max-w-[82%] ${isUser ? 'bg-[#3DE8D9] text-white' : 'bg-slate-800/60 border border-slate-400/30/40 text-slate-200'} rounded-xl px-3 py-2`}>
        {msg.image && (
          <div className="mb-1.5">
            <img src={msg.image} alt="Uploaded" className="max-w-[200px] rounded-lg border border-slate-600/50" />
          </div>
        )}
        {isUser ? (
          <p className="text-[13px] leading-snug">{msg.content}</p>
        ) : (
          <div className="prose prose-invert prose-xs max-w-none [&_p]:text-[13px] [&_p]:leading-snug [&_li]:text-[13px] [&_h1]:text-sm [&_h2]:text-sm [&_h3]:text-[13px] [&_code]:text-xs">
            <ReactMarkdown>{msg.content}</ReactMarkdown>
          </div>
        )}
        {!isUser && (
          <div className="mt-1 flex items-center gap-2">
            <button
              onClick={() => onCopy(idx, msg.content)}
              className="text-slate-400 hover:text-white text-[11px] flex items-center gap-1 transition-colors"
              data-testid={`copy-msg-${idx}`}
            >
              {copiedId === idx ? <><Check className="w-2.5 h-2.5" /> Copied</> : <><Copy className="w-2.5 h-2.5" /> Copy</>}
            </button>
            {isPro && onPin && (
              <button
                onClick={handlePin}
                className={`text-[11px] flex items-center gap-1 transition-colors ${
                  pinned ? 'text-[#3DE8D9]' : 'text-slate-500 hover:text-[#3DE8D9]'
                }`}
                title="Pin to memory"
                data-testid={`pin-msg-${idx}`}
              >
                <Pin className="w-2.5 h-2.5" />
                {pinned ? 'Pinned' : 'Pin'}
              </button>
            )}
            {msg.provider && (
              <span className="text-[9px] text-slate-500 ml-auto font-mono" data-testid={`provider-badge-${idx}`}>
                {msg.provider.model || msg.provider.name || 'AI'}
              </span>
            )}
            {msg.tools_used && msg.tools_used.length > 0 && (
              <div className="flex items-center gap-1 ml-auto" data-testid={`tools-badge-${idx}`}>
                {[...new Set(msg.tools_used)].map((tool) => (
                  <span key={tool} className="text-[8px] px-1 py-0.5 rounded bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/20 font-mono">
                    {tool.replace('calculate_', '').replace('get_', '').replace('_', ' ')}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {isUser && (
        <div className="w-6 h-6 bg-slate-700 rounded-md flex-shrink-0 flex items-center justify-center mt-0.5">
          <User className="w-3 h-3 text-slate-300" />
        </div>
      )}
    </div>
  );
};

const ChatMessages = ({ messages, showPatterns, copiedId, onCopy, isPro, onPin, onSuggestionClick, onFollowupClick, onActionClick }) => {
  const endRef = useRef(null);
  const shownChipsRef = useRef(new Set());
  const shownActionsRef = useRef(new Set());

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Log "shown" telemetry once per (message_idx, chip) pair when follow-up chips
  // are rendered. Fire-and-forget; silent on failure.
  useEffect(() => {
    const ctx = typeof window !== 'undefined' ? (window.__risedualActiveView || null) : null;
    messages.forEach((msg, idx) => {
      if (msg.role !== 'assistant') return;
      if (Array.isArray(msg.followups)) {
        msg.followups.forEach((chip) => {
          const key = `${idx}::${chip}`;
          if (shownChipsRef.current.has(key)) return;
          shownChipsRef.current.add(key);
          try {
            fetch(CHIP_API, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              credentials: 'include',
              body: JSON.stringify({
                action: 'shown',
                chip_text: chip,
                message_idx: idx,
                context_hub: ctx,
              }),
            }).catch((err) => { console.debug('[chip-telemetry] shown ping failed', err?.message); });
          } catch (err) { console.debug('[chip-telemetry] shown ping threw', err?.message); }
        });
      }
      if (Array.isArray(msg.actions)) {
        msg.actions.forEach((act) => {
          const label = act && act.label;
          if (!label) return;
          const key = `${idx}::action::${label}`;
          if (shownActionsRef.current.has(key)) return;
          shownActionsRef.current.add(key);
          try {
            fetch(CHIP_API, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              credentials: 'include',
              body: JSON.stringify({
                action: 'action-shown',
                chip_text: label,
                message_idx: idx,
                context_hub: ctx,
              }),
            }).catch((err) => { console.debug('[chip-telemetry] action-shown ping failed', err?.message); });
          } catch (err) { console.debug('[chip-telemetry] action-shown ping threw', err?.message); }
        });
      }
    });
  }, [messages]);

  if (messages.length === 0 && !showPatterns) {
    const suggestions = [
      'What is AAPL doing today?',
      'Analyze BTC chart patterns',
      'Best sector rotation?',
    ];
    if (isPro) suggestions.push('What do you remember about me?');

    return (
      <div className="flex-1 flex items-center justify-center p-4">
        <div className="text-center">
          <img src="/logo-ai-bright2.png" alt="RiseDualGPT" className="w-10 h-10 mx-auto mb-3 object-contain" />
          <h3 className="text-white text-sm font-semibold mb-1">RiseDualGPT</h3>
          <p className="text-slate-300 text-xs max-w-xs mx-auto leading-relaxed">
            Stocks, crypto, market trends, technical analysis, or upload a chart for pattern recognition.
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5 justify-center">
            {suggestions.map(q => (
              <button
                key={q}
                onClick={() => onSuggestionClick?.(q)}
                className={`text-[11px] px-2.5 py-1 rounded-full hover:bg-slate-700 transition-colors border ${
                  q.includes('remember') ? 'bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/30' : 'bg-slate-800 text-slate-300 border-slate-400/25'
                }`}
                data-testid={`suggestion-${q.slice(0, 15).replace(/\s/g, '-')}`}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2.5 scrollbar-thin scrollbar-thumb-slate-700" data-testid="chat-messages">
      {messages.map((msg, idx) => {
        const isLast = msg.role === 'assistant' && idx === messages.length - 1;
        // Only show follow-up chips on the MOST RECENT assistant message
        const showChips = isLast && Array.isArray(msg.followups) && msg.followups.length > 0;
        const showActions = isLast && Array.isArray(msg.actions) && msg.actions.length > 0;
        return (
          <React.Fragment key={`msg-${idx}-${msg.role}`}>
            <MessageBubble msg={msg} idx={idx} copiedId={copiedId} onCopy={onCopy} isPro={isPro} onPin={onPin} />
            {showActions && onActionClick && (
              <div className="flex items-start gap-2 px-2" data-testid={`actions-${idx}`}>
                <span className="text-[9px] text-[#3DE8D9]/70 uppercase tracking-wider font-semibold pt-2 shrink-0">Go</span>
                <div className="flex-1 flex flex-wrap gap-1.5">
                  {msg.actions.map((act, ai) => (
                    <button
                      key={`${idx}-${act.label}`}
                      onClick={() => onActionClick(act, idx)}
                      className="group text-[11px] font-semibold px-3 py-1.5 rounded-full bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/40 hover:bg-[#3DE8D9]/20 hover:border-[#3DE8D9] transition-colors inline-flex items-center gap-1.5"
                      data-testid={`action-btn-${idx}-${ai}`}
                      title={act.kind === 'research' ? `Open ${act.ticker || ''} in Research` : `Open ${act.kind}`}
                    >
                      <span>{act.label}</span>
                      <span className="opacity-60 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all">→</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {showChips && onFollowupClick && (
              <div className="flex items-start gap-2 px-2" data-testid={`followups-${idx}`}>
                <span className="text-[9px] text-slate-600 uppercase tracking-wider font-semibold pt-2 shrink-0">Next</span>
                <div className="flex-1 flex flex-wrap gap-1.5">
                  {msg.followups.map((chip, ci) => (
                    <button
                      key={`${idx}-${chip}`}
                      onClick={() => onFollowupClick(chip, idx)}
                      className="text-[11px] font-medium px-2.5 py-1.5 rounded-full bg-slate-800/80 text-slate-300 border border-slate-700/60 hover:text-[#3DE8D9] hover:border-[#3DE8D9]/40 hover:bg-[#3DE8D9]/5 transition-colors"
                      data-testid={`followup-chip-${idx}-${ci}`}
                    >
                      {chip}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </React.Fragment>
        );
      })}
      <div ref={endRef} />
    </div>
  );
};

export default ChatMessages;
export { ChatMessages };
