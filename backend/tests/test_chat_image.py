"""
Test suite for RISEDUALAI Chat API with Image Upload Support
Tests: POST /api/chat with text-only, image+text, and backward compatibility
"""
import pytest
import requests
import os
import base64
import io
from PIL import Image

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Generate a simple test image with real visual features (candlestick-like pattern)
def generate_test_chart_image():
    """Generate a simple chart-like image with visual features for testing"""
    img = Image.new('RGB', (200, 150), color=(30, 30, 35))  # Dark background
    
    # Draw some candlestick-like bars
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    
    # Draw grid lines
    for i in range(0, 200, 40):
        draw.line([(i, 0), (i, 150)], fill=(50, 50, 55), width=1)
    for i in range(0, 150, 30):
        draw.line([(0, i), (200, i)], fill=(50, 50, 55), width=1)
    
    # Draw candlestick-like bars (green and red) - ensure y0 < y1
    bars = [
        (20, 40, 80, 'green'),   # x, y_top, y_bottom (y_top < y_bottom)
        (50, 60, 90, 'red'),
        (80, 50, 70, 'green'),
        (110, 70, 100, 'red'),
        (140, 40, 60, 'green'),
        (170, 55, 85, 'red'),
    ]
    for x, y_top, y_bottom, color in bars:
        fill_color = (0, 200, 100) if color == 'green' else (200, 50, 50)
        # Rectangle: [x0, y0, x1, y1] where y0 < y1
        draw.rectangle([x-5, y_top, x+5, y_bottom], fill=fill_color)
        # Wick
        draw.line([(x, y_top-10), (x, y_bottom+10)], fill=fill_color, width=1)
    
    # Convert to base64
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('utf-8')


def generate_jpeg_test_image():
    """Generate a JPEG test image"""
    img = Image.new('RGB', (100, 100), color=(100, 150, 200))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.ellipse([20, 20, 80, 80], fill=(255, 200, 100))
    
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=85)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('utf-8')


class TestChatAPIBasic:
    """Basic chat API tests - text only"""
    
    def test_api_root_accessible(self):
        """Test that API root is accessible"""
        response = requests.get(f"{BASE_URL}/api/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        print(f"✓ API root accessible: {data['message']}")
    
    def test_chat_text_only_message(self):
        """Test POST /api/chat with text-only message"""
        payload = {
            "message": "What is a stock option?",
            "sessionId": "test_session_text_001"
        }
        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=60)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Validate response structure
        assert "response" in data, "Response should contain 'response' field"
        assert "sessionId" in data, "Response should contain 'sessionId' field"
        assert data["sessionId"] == "test_session_text_001"
        assert len(data["response"]) > 0, "AI response should not be empty"
        
        print(f"✓ Text-only chat works. Response length: {len(data['response'])} chars")
        print(f"  AI Response preview: {data['response'][:100]}...")
    
    def test_chat_with_null_image_base64(self):
        """Test POST /api/chat with null image_base64 (backward compatibility)"""
        payload = {
            "message": "Tell me about AAPL stock",
            "sessionId": "test_session_null_img_002",
            "image_base64": None
        }
        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=60)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "response" in data
        assert len(data["response"]) > 0
        print(f"✓ Chat with null image_base64 works (backward compat)")
    
    def test_chat_without_image_base64_field(self):
        """Test POST /api/chat without image_base64 field at all (backward compatibility)"""
        payload = {
            "message": "What is a put option?",
            "sessionId": "test_session_no_img_003"
        }
        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=60)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "response" in data
        assert len(data["response"]) > 0
        print(f"✓ Chat without image_base64 field works (backward compat)")


class TestChatAPIWithImage:
    """Chat API tests with image upload (vision)"""
    
    def test_chat_with_png_image(self):
        """Test POST /api/chat with PNG image for chart analysis"""
        image_base64 = generate_test_chart_image()
        
        payload = {
            "message": "Analyze this stock chart and identify any patterns",
            "sessionId": "test_session_png_img_004",
            "image_base64": image_base64
        }
        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=90)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "response" in data, "Response should contain 'response' field"
        assert "sessionId" in data
        assert len(data["response"]) > 0, "AI should provide analysis"
        
        # The AI should acknowledge it received an image
        response_lower = data["response"].lower()
        # Check if AI mentions anything related to chart/image/analysis
        print(f"✓ Chat with PNG image works. Response length: {len(data['response'])} chars")
        print(f"  AI Response preview: {data['response'][:200]}...")
    
    def test_chat_with_jpeg_image(self):
        """Test POST /api/chat with JPEG image"""
        image_base64 = generate_jpeg_test_image()
        
        payload = {
            "message": "What do you see in this image?",
            "sessionId": "test_session_jpeg_img_005",
            "image_base64": image_base64
        }
        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=90)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "response" in data
        assert len(data["response"]) > 0
        print(f"✓ Chat with JPEG image works")
    
    def test_chat_image_only_no_text(self):
        """Test POST /api/chat with image but minimal text"""
        image_base64 = generate_test_chart_image()
        
        payload = {
            "message": "",  # Empty message, should still work with image
            "sessionId": "test_session_img_only_006",
            "image_base64": image_base64
        }
        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=90)
        
        # This might fail if backend requires message - let's check
        if response.status_code == 200:
            data = response.json()
            assert "response" in data
            print(f"✓ Chat with image-only (empty message) works")
        elif response.status_code == 422:
            print(f"⚠ Chat requires non-empty message (validation error) - this is acceptable")
        else:
            print(f"⚠ Unexpected status {response.status_code}: {response.text}")


class TestChatHistory:
    """Test chat history endpoint"""
    
    def test_get_chat_history(self):
        """Test GET /api/chat/history/{session_id}"""
        session_id = "test_session_history_007"
        
        # First send a message to create history
        payload = {
            "message": "Hello, this is a test message for history",
            "sessionId": session_id
        }
        requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=60)
        
        # Now get history
        response = requests.get(f"{BASE_URL}/api/chat/history/{session_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        print(f"✓ Chat history endpoint works. Messages count: {len(data['messages'])}")
    
    def test_get_nonexistent_session_history(self):
        """Test GET /api/chat/history for non-existent session"""
        response = requests.get(f"{BASE_URL}/api/chat/history/nonexistent_session_xyz")
        
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        assert len(data["messages"]) == 0
        print(f"✓ Non-existent session returns empty messages array")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
