import React, { useRef, useEffect, useState, useCallback } from 'react';
import { User, Copy, Check, ImageIcon, Mic, MicOff, Volume2, VolumeX, Pin } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';

const ChatMessages = ({ messages, showPatterns, copiedId, onCopy, isPro, onPin, onSuggestionClick }) => {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
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
      {messages.map((msg, idx) => (
        <MessageBubble key={`msg-${idx}-${msg.role}`} msg={msg} idx={idx} copiedId={copiedId} onCopy={onCopy} isPro={isPro} onPin={onPin} />
      ))}
      <div ref={endRef} />
    </div>
  );
};

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

const VoiceSelector = ({ voiceMode, setVoiceMode, isSpeaking, onStopSpeaking }) => {
  const cycle = () => {
    if (isSpeaking) { onStopSpeaking(); return; }
    const order = [null, 'female', 'male'];
    const idx = order.indexOf(voiceMode);
    setVoiceMode(order[(idx + 1) % order.length]);
  };

  const label = voiceMode === 'female' ? 'F' : voiceMode === 'male' ? 'M' : '';
  const color = isSpeaking ? 'text-[#3DE8D9] animate-pulse' : voiceMode ? 'text-[#3DE8D9]' : 'text-slate-400';
  const Icon = voiceMode ? Volume2 : VolumeX;

  return (
    <button
      onClick={cycle}
      className={`relative h-7 px-1.5 flex items-center gap-0.5 rounded-md hover:bg-slate-700/50 transition-colors ${color}`}
      title={isSpeaking ? 'Stop speaking' : voiceMode ? `Voice: ${voiceMode} (click to change)` : 'Voice off (click to enable)'}
      data-testid="voice-toggle"
    >
      <Icon className="w-3.5 h-3.5" />
      {label && <span className="text-[9px] font-bold">{label}</span>}
    </button>
  );
};

const ChatInputArea = ({ input, setInput, onSend, loading, imagePreview, onImageSelect, onClearImage, inputRef, apiBase }) => {
  const fileRef = useRef(null);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        if (blob.size < 1000) return; // too short

        setIsTranscribing(true);
        try {
          const formData = new FormData();
          formData.append('audio', blob, 'recording.webm');
          const res = await authFetch(`${apiBase}/chat/stt`, {
            method: 'POST',
            body: formData,
          });
          if (res.ok) {
            const data = await res.json();
            if (data.text) {
              setInput(prev => prev ? `${prev} ${data.text}` : data.text);
              inputRef.current?.focus();
            }
          }
        } catch (err) {
          logger.error('STT error:', err);
        } finally {
          setIsTranscribing(false);
        }
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (err) {
      logger.error('Microphone access denied:', err);
    }
  }, [apiBase, setInput, inputRef]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    setIsRecording(false);
  }, []);

  return (
    <div className="border-t border-slate-400/25 px-3 py-2 pb-10 lg:pb-2 flex-shrink-0" data-testid="chat-input-area">
      {imagePreview && (
        <div className="mb-2 flex items-center gap-2 bg-slate-700/60 rounded-lg p-1.5">
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
        <button
          onClick={isRecording ? stopRecording : startRecording}
          disabled={isTranscribing}
          className={`flex-shrink-0 w-8 h-8 border rounded-lg flex items-center justify-center transition-colors ${
            isRecording
              ? 'bg-red-500/20 border-red-500/50 text-red-400 animate-pulse'
              : isTranscribing
              ? 'bg-slate-800 border-slate-400/25 text-amber-400 animate-pulse'
              : 'bg-slate-800 border-slate-400/25 text-slate-400 hover:text-white hover:border-slate-600'
          }`}
          title={isRecording ? 'Stop recording' : isTranscribing ? 'Transcribing...' : 'Voice input'}
          data-testid="chat-mic-btn"
        >
          {isRecording ? <MicOff className="w-3.5 h-3.5" /> : <Mic className="w-3.5 h-3.5" />}
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

export { ChatMessages, ChatInputArea, VoiceSelector };
