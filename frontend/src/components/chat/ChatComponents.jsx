import React, { useRef, useEffect } from 'react';
import { Bot, User, Copy, Check, ImageIcon } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

const ChatMessages = ({ messages, showPatterns, copiedId, onCopy }) => {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  if (messages.length === 0 && !showPatterns) {
    return (
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="text-center">
          <Bot className="w-12 h-12 text-[#0052FF] mx-auto mb-4 opacity-60" />
          <h3 className="text-white text-lg font-semibold mb-2">RISEDUAL AI Assistant</h3>
          <p className="text-slate-400 text-sm max-w-md mx-auto">
            Ask about stocks, crypto, market trends, technical analysis, or upload a chart image for pattern recognition.
          </p>
          <div className="mt-4 flex flex-wrap gap-2 justify-center">
            {['What is AAPL doing today?', 'Analyze BTC chart patterns', 'Best sector rotation strategy?'].map(q => (
              <button
                key={q}
                className="text-xs bg-slate-800 text-slate-300 px-3 py-1.5 rounded-full hover:bg-slate-700 transition-colors border border-slate-700/50"
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
    <div className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-thin scrollbar-thumb-slate-700" data-testid="chat-messages">
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
    <div className={`flex gap-3 ${isUser ? 'justify-end' : ''}`} data-testid={`message-${idx}`}>
      {!isUser && (
        <div className="w-8 h-8 bg-[#0052FF]/20 rounded-lg flex-shrink-0 flex items-center justify-center">
          <Bot className="w-4 h-4 text-[#0052FF]" />
        </div>
      )}
      <div className={`max-w-[80%] ${isUser ? 'bg-[#0052FF] text-white' : 'bg-slate-800/60 border border-slate-700/40 text-slate-200'} rounded-2xl px-4 py-3`}>
        {msg.image && (
          <div className="mb-2">
            <img src={msg.image} alt="Uploaded" className="max-w-[250px] rounded-lg border border-slate-600/50" />
          </div>
        )}
        {isUser ? (
          <p className="text-sm">{msg.content}</p>
        ) : (
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{msg.content}</ReactMarkdown>
          </div>
        )}
        {!isUser && (
          <button
            onClick={() => onCopy(idx, msg.content)}
            className="mt-2 text-slate-500 hover:text-white text-xs flex items-center gap-1 transition-colors"
            data-testid={`copy-msg-${idx}`}
          >
            {copiedId === idx ? <><Check className="w-3 h-3" /> Copied</> : <><Copy className="w-3 h-3" /> Copy</>}
          </button>
        )}
      </div>
      {isUser && (
        <div className="w-8 h-8 bg-slate-700 rounded-lg flex-shrink-0 flex items-center justify-center">
          <User className="w-4 h-4 text-slate-300" />
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
    <div className="border-t border-slate-700/50 p-4" data-testid="chat-input-area">
      {imagePreview && (
        <div className="mb-3 flex items-center gap-2 bg-slate-800/50 rounded-lg p-2">
          <img src={imagePreview} alt="Preview" className="h-12 rounded border border-slate-600" />
          <span className="text-slate-400 text-xs">Image attached</span>
          <button onClick={onClearImage} className="text-red-400 text-xs hover:text-red-300 ml-auto">Remove</button>
        </div>
      )}
      <div className="flex gap-2 items-end">
        <button
          onClick={() => fileRef.current?.click()}
          className="flex-shrink-0 w-10 h-10 bg-slate-800 border border-slate-700/50 rounded-xl flex items-center justify-center text-slate-400 hover:text-white hover:border-slate-600 transition-colors"
          title="Upload chart image"
          data-testid="chat-image-upload"
        >
          <ImageIcon className="w-4 h-4" />
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
          placeholder="Ask about stocks, crypto, or upload a chart..."
          className="flex-1 bg-slate-800 border border-slate-700/50 rounded-xl px-4 py-2.5 text-white text-sm placeholder-slate-500 resize-none focus:outline-none focus:border-[#0052FF] min-h-[44px] max-h-[120px]"
          rows={1}
          data-testid="chat-input"
        />
        <button
          onClick={onSend}
          disabled={loading || (!input.trim() && !imagePreview)}
          className="flex-shrink-0 w-10 h-10 bg-[#0052FF] hover:bg-[#2563EB] rounded-xl flex items-center justify-center text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="chat-send"
        >
          {loading ? (
            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          ) : (
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
            </svg>
          )}
        </button>
      </div>
    </div>
  );
};

export { ChatMessages, ChatInputArea };
