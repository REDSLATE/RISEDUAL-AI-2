import React, { useRef, useEffect, useState, useCallback } from 'react';
import { ImageIcon, Mic, MicOff, AlertCircle, X } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';

// Renamed on export for clarity: ChatInput. Legacy `ChatInputArea` name is
// still re-exported so existing imports keep working during the roll-out.
const ChatInput = ({ input, setInput, onSend, loading, imagePreview, onImageSelect, onClearImage, inputRef, apiBase }) => {
  const fileRef = useRef(null);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [precheck, setPrecheck] = useState(null);
  const precheckTimer = useRef(null);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);

  // Debounced precheck — when the user types a question the Help Center has
  // marked as a known gap, surface a transparent hint above the input.
  useEffect(() => {
    if (precheckTimer.current) clearTimeout(precheckTimer.current);
    const q = (input || '').trim();
    if (q.length < 8) { setPrecheck(null); return; }
    precheckTimer.current = setTimeout(async () => {
      try {
        const r = await fetch(`${apiBase}/analytics/help-search/suggestions?q=${encodeURIComponent(q)}`, {
          credentials: 'include',
        });
        if (r.ok) {
          const data = await r.json();
          setPrecheck(data.gap_signal ? data : null);
        }
      } catch (err) {
        // Precheck is a UX hint, not critical path. Dev-only log so
        // noisy network blips don't spam prod consoles but real bugs
        // are still diagnosable when hacking locally.
        logger.warn('chat precheck suggestion fetch failed', err);
      }
    }, 650);
    return () => precheckTimer.current && clearTimeout(precheckTimer.current);
  }, [input, apiBase]);

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
    <div className="border-t border-slate-400/25 px-3 py-2 flex-shrink-0" data-testid="chat-input-area">
      {imagePreview && (
        <div className="mb-2 flex items-center gap-2 bg-slate-700/60 rounded-lg p-1.5">
          <img src={imagePreview} alt="Preview" className="h-10 rounded border border-slate-600" />
          <span className="text-slate-400 text-[11px]">Image attached</span>
          <button onClick={onClearImage} className="text-orange-400 text-[11px] hover:text-orange-300 ml-auto">Remove</button>
        </div>
      )}

      {/* Known-gap transparency banner — only shown when the user's draft matches
          a frequently-unanswered Help Center query. Non-blocking, dismissible. */}
      {precheck?.gap_signal && (
        <div className="mb-2 flex items-start gap-2 bg-orange-500/10 border border-orange-500/30 rounded-lg px-2.5 py-1.5" data-testid="chat-gap-hint">
          <AlertCircle className="w-3.5 h-3.5 text-orange-400 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0 text-[11px] leading-relaxed">
            <span className="text-orange-300 font-semibold">Heads up:</span>
            <span className="text-orange-200/90 ml-1">
              {precheck.gap_signal.unique_users} other user{precheck.gap_signal.unique_users === 1 ? ' has' : 's have'} asked about this recently — no Help docs yet. I&rsquo;ll do my best.
            </span>
          </div>
          <button
            onClick={() => setPrecheck(null)}
            className="text-orange-400/70 hover:text-orange-300 p-0.5 shrink-0"
            aria-label="Dismiss"
          >
            <X className="w-3 h-3" />
          </button>
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

export default ChatInput;
// Legacy alias — `RiseDualGPTChat.jsx` imports as `ChatInputArea`; keep working.
export { ChatInput, ChatInput as ChatInputArea };
