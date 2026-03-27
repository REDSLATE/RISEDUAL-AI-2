import React, { useState, useRef } from 'react';
import { Send, Sparkles, Image, X } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { sendChatMessage } from '../services/api';

const TradeGPTChat = () => {
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: 'Hello! I\'m RISEDUALAI, your AI-powered trading assistant. Ask me anything about stocks, options, market analysis, or trading strategies. You can also upload chart screenshots for analysis!',
    },
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => `session_${Date.now()}_${Math.random().toString(36).substring(7)}`);
  const [isOpen, setIsOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState(null);
  const [imageBase64, setImageBase64] = useState(null);
  const fileInputRef = useRef(null);

  const handleImageUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const validTypes = ['image/jpeg', 'image/png', 'image/webp'];
    if (!validTypes.includes(file.type)) {
      alert('Please upload a JPEG, PNG, or WEBP image.');
      return;
    }

    if (file.size > 10 * 1024 * 1024) {
      alert('Image must be under 10MB.');
      return;
    }

    const reader = new FileReader();
    reader.onload = (event) => {
      const dataUrl = event.target.result;
      setImagePreview(dataUrl);
      // Extract pure base64 (remove "data:image/...;base64," prefix)
      const base64 = dataUrl.split(',')[1];
      setImageBase64(base64);
    };
    reader.readAsDataURL(file);
    // Reset file input so same file can be re-selected
    e.target.value = '';
  };

  const clearImage = () => {
    setImagePreview(null);
    setImageBase64(null);
  };

  const handleSend = async () => {
    if (!input.trim() && !imageBase64) return;

    const userMessage = {
      role: 'user',
      content: input || (imageBase64 ? 'Analyze this chart' : ''),
      image: imagePreview || null,
    };
    setMessages((prev) => [...prev, userMessage]);

    const currentMessage = input || 'Please analyze this chart/image and provide trading insights.';
    const currentImage = imageBase64;

    setInput('');
    clearImage();
    setIsLoading(true);

    try {
      const response = await sendChatMessage(currentMessage, sessionId, currentImage);
      const assistantMessage = {
        role: 'assistant',
        content: response.response,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      console.error('Error sending message:', error);
      const errorMessage = {
        role: 'assistant',
        content: 'I apologize, but I encountered an error. Please try again.',
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed bottom-6 right-6 z-40">
      <div className="mb-4 flex justify-end">
        <Button
          data-testid="chat-toggle-btn"
          className="bg-blue-600 hover:bg-blue-700 text-white rounded-full w-14 h-14 shadow-lg"
          onClick={() => setIsOpen(!isOpen)}
        >
          <Sparkles className="w-6 h-6" />
        </Button>
      </div>

      {isOpen && <Card
        id="chat-window"
        data-testid="chat-window"
        className="w-96 h-[500px] bg-[#0a0a0b] border-gray-800 flex flex-col shadow-2xl z-50"
      >
        <div className="bg-blue-600 text-white px-4 py-3 rounded-t-lg">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5" />
            <h3 className="font-semibold">RISEDUALAI</h3>
          </div>
          <p className="text-xs text-blue-100 mt-1">AI Trading Assistant (Powered by GPT-5.2) — Supports Image Analysis</p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4" data-testid="chat-messages">
          {messages.map((message, index) => (
            <div
              key={index}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[80%] rounded-lg px-4 py-2 ${
                  message.role === 'user'
                    ? 'bg-blue-600 text-white'
                    : 'bg-[#272729] text-gray-200'
                }`}
              >
                {message.image && (
                  <img
                    src={message.image}
                    alt="Uploaded chart"
                    className="rounded mb-2 max-h-40 w-auto"
                    data-testid={`chat-image-${index}`}
                  />
                )}
                <p className="text-sm whitespace-pre-wrap">{message.content}</p>
              </div>
            </div>
          ))}
          {isLoading && (
            <div className="flex justify-start">
              <div className="bg-[#272729] text-gray-200 rounded-lg px-4 py-2">
                <div className="flex gap-1">
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }} />
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.4s' }} />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Image Preview */}
        {imagePreview && (
          <div className="px-4 py-2 border-t border-gray-800" data-testid="image-preview-area">
            <div className="relative inline-block">
              <img
                src={imagePreview}
                alt="Upload preview"
                className="h-16 w-auto rounded border border-gray-700"
                data-testid="image-preview-thumbnail"
              />
              <button
                onClick={clearImage}
                className="absolute -top-2 -right-2 bg-red-500 hover:bg-red-600 text-white rounded-full w-5 h-5 flex items-center justify-center"
                data-testid="clear-image-btn"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>
        )}

        {/* Input Area */}
        <div className="border-t border-gray-800 p-4">
          <div className="flex gap-2">
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleImageUpload}
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              data-testid="image-file-input"
            />
            <Button
              onClick={() => fileInputRef.current?.click()}
              variant="outline"
              size="icon"
              className="border-gray-700 bg-[#272729] hover:bg-[#3a3a3c] text-gray-300 shrink-0"
              title="Upload chart screenshot"
              data-testid="upload-image-btn"
            >
              <Image className="w-4 h-4" />
            </Button>
            <Input
              type="text"
              placeholder="Ask about stocks, options..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && handleSend()}
              className="flex-1 bg-[#272729] border-gray-700 text-white placeholder-gray-500"
              data-testid="chat-input"
            />
            <Button
              onClick={handleSend}
              disabled={isLoading || (!input.trim() && !imageBase64)}
              className="bg-blue-600 hover:bg-blue-700 text-white"
              data-testid="chat-send-btn"
            >
              <Send className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </Card>}
    </div>
  );
};

export default TradeGPTChat;
