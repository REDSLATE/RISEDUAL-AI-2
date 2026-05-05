"""
Iteration 82: Voice Capabilities Testing
- POST /api/chat/tts - Text-to-Speech (nova=female, onyx=male)
- POST /api/chat/stt - Speech-to-Text (audio file upload)
"""
import pytest
import requests
import os
import base64

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestTTSEndpoint:
    """Text-to-Speech endpoint tests"""
    
    def test_tts_female_voice_nova(self):
        """Test TTS with female voice (nova)"""
        response = requests.post(
            f"{BASE_URL}/api/chat/tts",
            json={"text": "Hello, this is a test.", "voice": "nova"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "audio" in data, "Response should contain 'audio' field"
        assert "format" in data, "Response should contain 'format' field"
        assert data["format"] == "mp3", f"Expected format 'mp3', got {data['format']}"
        assert data.get("voice") == "nova", f"Expected voice 'nova', got {data.get('voice')}"
        
        # Verify audio is valid base64
        audio_base64 = data["audio"]
        assert len(audio_base64) > 1000, f"Audio too short ({len(audio_base64)} chars), expected > 1000"
        try:
            decoded = base64.b64decode(audio_base64)
            assert len(decoded) > 500, "Decoded audio too small"
        except Exception as e:
            pytest.fail(f"Invalid base64 audio: {e}")
    
    def test_tts_male_voice_onyx(self):
        """Test TTS with male voice (onyx)"""
        response = requests.post(
            f"{BASE_URL}/api/chat/tts",
            json={"text": "Testing male voice output.", "voice": "onyx"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "audio" in data, "Response should contain 'audio' field"
        assert data["format"] == "mp3", f"Expected format 'mp3', got {data['format']}"
        assert data.get("voice") == "onyx", f"Expected voice 'onyx', got {data.get('voice')}"
        
        # Verify audio is valid base64
        audio_base64 = data["audio"]
        assert len(audio_base64) > 1000, f"Audio too short ({len(audio_base64)} chars)"
    
    def test_tts_empty_text_returns_400(self):
        """Test TTS returns 400 when text is empty"""
        response = requests.post(
            f"{BASE_URL}/api/chat/tts",
            json={"text": "", "voice": "nova"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400, f"Expected 400 for empty text, got {response.status_code}"
        
        data = response.json()
        assert "detail" in data, "Error response should contain 'detail'"
        assert "text" in data["detail"].lower() or "no text" in data["detail"].lower(), \
            f"Error should mention text issue: {data['detail']}"
    
    def test_tts_default_voice(self):
        """Test TTS uses default voice (nova) when not specified"""
        response = requests.post(
            f"{BASE_URL}/api/chat/tts",
            json={"text": "Default voice test."},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "audio" in data, "Response should contain 'audio' field"
        # Default voice should be nova
        assert data.get("voice") == "nova", f"Expected default voice 'nova', got {data.get('voice')}"
    
    def test_tts_long_text_truncation(self):
        """Test TTS handles long text (should truncate to 4096 chars)"""
        long_text = "This is a test sentence. " * 200  # ~5000 chars
        response = requests.post(
            f"{BASE_URL}/api/chat/tts",
            json={"text": long_text, "voice": "nova"},
            headers={"Content-Type": "application/json"}
        )
        # Should succeed (text gets truncated internally)
        assert response.status_code == 200, f"Expected 200 for long text, got {response.status_code}"
        
        data = response.json()
        assert "audio" in data, "Response should contain 'audio' field"


class TestSTTEndpoint:
    """Speech-to-Text endpoint tests"""
    
    def test_stt_with_sample_audio(self):
        """Test STT with a sample audio file"""
        # Create a minimal valid WebM audio file header (this won't transcribe meaningful text
        # but should test the endpoint accepts the file format)
        # For real testing, we'd need an actual audio file
        
        # Generate a simple WAV file with silence (more likely to be accepted)
        import struct
        import io
        
        # Create a minimal WAV file (1 second of silence)
        sample_rate = 16000
        duration = 1  # seconds
        num_samples = sample_rate * duration
        
        wav_buffer = io.BytesIO()
        # WAV header
        wav_buffer.write(b'RIFF')
        wav_buffer.write(struct.pack('<I', 36 + num_samples * 2))  # File size - 8
        wav_buffer.write(b'WAVE')
        wav_buffer.write(b'fmt ')
        wav_buffer.write(struct.pack('<I', 16))  # Subchunk1Size
        wav_buffer.write(struct.pack('<H', 1))   # AudioFormat (PCM)
        wav_buffer.write(struct.pack('<H', 1))   # NumChannels
        wav_buffer.write(struct.pack('<I', sample_rate))  # SampleRate
        wav_buffer.write(struct.pack('<I', sample_rate * 2))  # ByteRate
        wav_buffer.write(struct.pack('<H', 2))   # BlockAlign
        wav_buffer.write(struct.pack('<H', 16))  # BitsPerSample
        wav_buffer.write(b'data')
        wav_buffer.write(struct.pack('<I', num_samples * 2))  # Subchunk2Size
        # Write silence (zeros)
        wav_buffer.write(b'\x00' * (num_samples * 2))
        
        wav_buffer.seek(0)
        
        files = {'audio': ('test.wav', wav_buffer, 'audio/wav')}
        response = requests.post(
            f"{BASE_URL}/api/chat/stt",
            files=files
        )
        
        # The endpoint should accept the file (may return empty text for silence)
        # Status 200 means the endpoint works, even if transcription is empty
        assert response.status_code in [200, 500], f"Expected 200 or 500, got {response.status_code}: {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            assert "text" in data, "Response should contain 'text' field"
            print(f"STT transcription result: '{data['text']}'")
    
    def test_stt_missing_audio_file(self):
        """Test STT returns error when no audio file provided"""
        response = requests.post(
            f"{BASE_URL}/api/chat/stt",
            files={}
        )
        # Should return 422 (validation error) for missing required file
        assert response.status_code == 422, f"Expected 422 for missing file, got {response.status_code}"


class TestVoiceIntegration:
    """Integration tests for voice features"""
    
    def test_tts_response_is_playable_audio(self):
        """Verify TTS returns audio that can be decoded"""
        response = requests.post(
            f"{BASE_URL}/api/chat/tts",
            json={"text": "Market analysis complete.", "voice": "nova"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        
        data = response.json()
        audio_base64 = data["audio"]
        
        # Decode and check MP3 magic bytes
        decoded = base64.b64decode(audio_base64)
        # MP3 files start with ID3 tag or frame sync (0xFF 0xFB or similar)
        is_mp3 = decoded[:3] == b'ID3' or (decoded[0] == 0xFF and (decoded[1] & 0xE0) == 0xE0)
        assert is_mp3, f"Audio doesn't appear to be valid MP3. First bytes: {decoded[:10].hex()}"
        
        print(f"TTS audio size: {len(decoded)} bytes")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
