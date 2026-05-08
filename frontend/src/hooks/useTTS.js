/**
 * useTTS — text-to-speech hook for the chat.
 *
 * Streams text → /api/chat/tts, plays back base64 mp3, exposes speaking state.
 */
import { useState, useRef, useCallback } from 'react';
import { authFetch } from '../contexts/AuthContext';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;
const VOICE_MAP = { female: 'nova', male: 'onyx' };

export default function useTTS() {
  const [voiceMode, setVoiceMode] = useState(null);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const audioRef = useRef(null);

  const playTTS = useCallback(async (text) => {
    if (!voiceMode) return;
    try {
      setIsSpeaking(true);
      const clean = text.replace(/[#*_`\[\]()>~|]/g, '').replace(/\n{2,}/g, '. ').substring(0, 4096);
      const res = await authFetch(`${API}/chat/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: clean, voice: VOICE_MAP[voiceMode] }),
      });
      if (!res.ok) throw new Error('TTS failed');
      const data = await res.json();
      const audio = new Audio(`data:audio/mp3;base64,${data.audio}`);
      audioRef.current = audio;
      audio.onended = () => setIsSpeaking(false);
      audio.onerror = () => setIsSpeaking(false);
      await audio.play();
    } catch (err) {
      logger.error('TTS error:', err);
      setIsSpeaking(false);
    }
  }, [voiceMode]);

  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setIsSpeaking(false);
  }, []);

  return { voiceMode, setVoiceMode, isSpeaking, playTTS, stopSpeaking };
}
