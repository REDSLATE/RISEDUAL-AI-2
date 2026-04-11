import React, { useRef, useEffect } from 'react';
import { User, Copy, Check, ImageIcon } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

const ChatMessages = ({ messages, showPatterns, copiedId, onCopy }) => {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  if (messages.length === 0 && !showPatterns) {
    return (
      <div className="flex-1 flex items-center justify-center p-4">
        <div className="text-center">
          <img src="/logo-ai-bright2.png" alt="RISEDUAL AI" className="w-10 h-10 mx-auto mb-3 object-contain" />
          <h3 className="text-white text-sm font-semibold mb-1">RISEDUAL AI Assistant</h3>
          <p className="text-slate-300 text-xs max-w-xs mx-auto leading-relaxed">
            Stocks, crypto, market trends, technical analysis, or upload a chart for pattern recognition.
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5 justify-center">
            {['What is AAPL doing today?', 'Analyze BTC chart patterns', 'Best sector rotation?'].map(q => (
              <button
                key={q}
                className="text-[11px] bg-slate-800 text-slate-300 px-2.5 py-1 rounded-full hover:bg-slate-700 transition-colors border border-slate-400/25"
                data-testid={`suggestion-${q.slice(0, 10)}`}
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
      {messages.map((msg, idx) => (
        <MessageBubble key={`msg-${idx}-${msg.role}`} msg={msg} idx={idx} copiedId={copiedId} onCopy={onCopy} />
      ))}
      <div ref={endRef} />
    </div>
  );
};

const MessageBubble = ({ msg, idx, copiedId, onCopy }) => {
  const isUser = msg.role === 'user';

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
          <button
            onClick={() => onCopy(idx, msg.content)}
            className="mt-1 text-slate-400 hover:text-white text-[11px] flex items-center gap-1 transition-colors"
            data-testid={`copy-msg-${idx}`}
          >
            {copiedId === idx ? <><Check className="w-2.5 h-2.5" /> Copied</> : <><Copy className="w-2.5 h-2.5" /> Copy</>}
          </button>
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

const ChatInputArea = ({ input, setInput, onSend, loading, imagePreview, onImageSelect, onClearImage, inputRef }) => {
  const fileRef = useRef(null);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div className="border-t border-slate-400/25 px-3 py-2 pb-10 lg:pb-2 flex-shrink-0" data-testid="chat-input-area">
      {imagePreview && (
        <div className="mb-2 flex items-center gap-2 bg-slate-700/45 rounded-lg p-1.5">
          <img src={imagePreview} alt="Preview" className="h-10 rounded border border-slate-600" />
          <span className="text-slate-400 text-[11px]">Image attached</span>
          <button onClick={onClearImage} className="text-orange-400 text-[11px] hover:text-orange-300 ml-auto">Remove</button>
        </div>
      )}
      <div className="flex gap-1.5 items-end">
        <button
          onClick={() => fileRef.current?.click()}
          className="flex-shrink-0 w-8 h-8 bg-slate-800 border border-slate-400/25 rounded-lg flex items-center justify-center text-slate-400 hover:text-white hover:border-slate-600 transition-colors"
          title="Upload chart image"
          data-testid="chat-image-upload"
        >
          <ImageIcon className="w-3.5 h-3.5" />
        </button>
        <input
          type="file"
          ref={fileRef}
          accept="image/*"
          onChange={onImageSelect}
          className="hidden"
        />
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about stocks, crypto, charts..."
          className="flex-1 bg-slate-800 border border-slate-400/25 rounded-lg px-3 py-1.5 text-white text-[13px] placeholder-slate-500 resize-none focus:outline-none focus:border-[#3DE8D9] min-h-[34px] max-h-[100px]"
          rows={1}
          data-testid="chat-input"
        />
        <button
          onClick={onSend}
          disabled={loading || (!input.trim() && !imagePreview)}
          className="flex-shrink-0 w-8 h-8 bg-[#3DE8D9] hover:bg-[#7AEEE0] rounded-lg flex items-center justify-center text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="chat-send"
        >
          {loading ? (
            <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          ) : (
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
            </svg>
          )}
        </button>
      </div>
    </div>
  );
};

export { ChatMessages, ChatInputArea };
