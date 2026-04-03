import React, { useState, useRef, useEffect } from 'react';
import { Send, Sparkles, Image, X, BarChart3, Building2 } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { sendChatMessage, researchCompany } from '../services/api';
import ChartPatternLibrary from './ChartPatternLibrary';
import { ResearchCard } from './CompanyResearch';

const TradeGPTChat = () => {
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: 'Hello! I\'m RISEDUAL AI, your AI-powered trading assistant. Ask me anything about stocks, options, market analysis, or trading strategies.\n\nTip: Type /patterns to browse common chart patterns, or upload a screenshot for AI analysis!',
    },
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => `session_${Date.now()}_${Math.random().toString(36).substring(7)}`);
  const [isOpen, setIsOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState(null);
  const [imageBase64, setImageBase64] = useState(null);
  const fileInputRef = useRef(null);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    const handleOpenChat = () => setIsOpen(true);
    window.addEventListener('risedualai-open-chat', handleOpenChat);
    return () => window.removeEventListener('risedualai-open-chat', handleOpenChat);
  }, []);

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
      const base64 = dataUrl.split(',')[1];
      setImageBase64(base64);
    };
    reader.readAsDataURL(file);
    e.target.value = '';
  };

  const clearImage = () => {
    setImagePreview(null);
    setImageBase64(null);
  };

  const handlePatternSelect = (pattern) => {
    const prompt = `Explain the "${pattern.name}" chart pattern in detail. Include: 1) How to identify it, 2) What it signals (${pattern.type}), 3) Entry/exit strategies, 4) Stop-loss placement, 5) Real-world examples of this pattern. Keep it actionable for a trader.`;
    setInput('');
    
    const userMessage = {
      role: 'user',
      content: `Tell me about the ${pattern.name} pattern`,
    };
    setMessages((prev) => [...prev, userMessage]);
    setIsLoading(true);

    sendChatMessage(prompt, sessionId)
      .then((response) => {
        setMessages((prev) => [...prev, { role: 'assistant', content: response.response }]);
      })
      .catch(() => {
        setMessages((prev) => [...prev, { role: 'assistant', content: 'Sorry, I encountered an error analyzing that pattern. Please try again.' }]);
      })
      .finally(() => setIsLoading(false));
  };

  const handleSend = async () => {
    if (!input.trim() && !imageBase64) return;

    // Check for /patterns command
    if (input.trim().toLowerCase() === '/patterns') {
      setMessages((prev) => [...prev, 
        { role: 'user', content: '/patterns' },
        { role: 'assistant', content: '__PATTERN_LIBRARY__' }
      ]);
      setInput('');
      return;
    }

    // Check for /research TICKER command
    const researchMatch = input.trim().match(/^\/research\s+(\w+)$/i);
    if (researchMatch) {
      const ticker = researchMatch[1].toUpperCase();
      setMessages((prev) => [...prev, { role: 'user', content: `/research ${ticker}` }]);
      setInput('');
      setIsLoading(true);
      try {
        const data = await researchCompany(ticker);
        setMessages((prev) => [...prev, { role: 'assistant', content: '__RESEARCH__', researchData: data }]);
      } catch {
        setMessages((prev) => [...prev, { role: 'assistant', content: `Could not find research data for ${ticker}.` }]);
      } finally {
        setIsLoading(false);
      }
      return;
    }

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
      setMessages((prev) => [...prev, { role: 'assistant', content: response.response }]);
    } catch (error) {
      console.error('Error sending message:', error);
      setMessages((prev) => [...prev, { role: 'assistant', content: 'I apologize, but I encountered an error. Please try again.' }]);
    } finally {
      setIsLoading(false);
    }
  };

  const renderMessageContent = (message, index) => {
    if (message.content === '__PATTERN_LIBRARY__') {
      return <ChartPatternLibrary onSelectPattern={handlePatternSelect} />;
    }

    if (message.content === '__RESEARCH__' && message.researchData) {
      return <ResearchCard data={message.researchData} compact={true} />;
    }

    return (
      <>
        {message.image && (
          <img
            src={message.image}
            alt="Uploaded chart"
            className="rounded mb-2 max-h-40 w-auto"
            data-testid={`chat-image-${index}`}
          />
        )}
        <p className="text-sm whitespace-pre-wrap">{message.content}</p>
      </>
    );
  };

  return (
    <div className="fixed bottom-6 right-6 z-40 max-lg:bottom-20 max-lg:right-3">
      <div className="mb-4 flex justify-end">
        <Button
          data-testid="chat-toggle-btn"
          className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-full w-14 h-14 shadow-lg hidden lg:flex"
          onClick={() => setIsOpen(!isOpen)}
        >
          <Sparkles className="w-6 h-6" />
        </Button>
      </div>

      {isOpen && <Card
        data-testid="chat-window"
        className="w-96 max-lg:w-[calc(100vw-1.5rem)] max-lg:max-w-none h-[500px] max-lg:h-[70vh] bg-slate-900 border-slate-700/50 flex flex-col shadow-2xl z-50 rounded-xl max-lg:fixed max-lg:bottom-20 max-lg:right-3 max-lg:left-3"
      >
        <div className="bg-[#0052FF] text-white px-4 py-3 rounded-t-xl">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5" />
            <h3 className="font-semibold">RISEDUAL AI</h3>
          </div>
          <p className="text-xs text-blue-200 mt-1">AI Trading Assistant — Image Analysis & Pattern Library</p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4" data-testid="chat-messages">
          {messages.map((message, index) => (
            <div
              key={`msg-${index}-${message.role}`}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`rounded-lg px-4 py-2 ${
                  message.content === '__PATTERN_LIBRARY__' || message.content === '__RESEARCH__'
                    ? 'max-w-[95%]'
                    : 'max-w-[80%]'
                } ${
                  message.role === 'user'
                    ? 'bg-[#0052FF] text-white'
                    : 'bg-[#1E293B] text-slate-200'
                }`}
              >
                {renderMessageContent(message, index)}
              </div>
            </div>
          ))}
          {isLoading && (
            <div className="flex justify-start">
              <div className="bg-[#1E293B] text-slate-200 rounded-lg px-4 py-2">
                <div className="flex gap-1">
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }} />
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.4s' }} />
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {imagePreview && (
          <div className="px-4 py-2 border-t border-slate-700" data-testid="image-preview-area">
            <div className="relative inline-block">
              <img
                src={imagePreview}
                alt="Upload preview"
                className="h-16 w-auto rounded border border-slate-600"
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

        <div className="border-t border-slate-700 p-3">
          <div className="flex gap-2 mb-2">
            <button
              onClick={() => {
                setMessages((prev) => [...prev, 
                  { role: 'user', content: '/patterns' },
                  { role: 'assistant', content: '__PATTERN_LIBRARY__' }
                ]);
              }}
              className="text-[10px] px-2 py-1 rounded-full border border-slate-600 text-slate-400 hover:text-blue-400 hover:border-blue-500 transition-colors flex items-center gap-1"
              data-testid="patterns-shortcut-btn"
            >
              <BarChart3 className="w-3 h-3" />
              /patterns
            </button>
            <button
              onClick={() => setInput('/research ')}
              className="text-[10px] px-2 py-1 rounded-full border border-slate-600 text-slate-400 hover:text-[#0052FF] hover:border-[#0052FF] transition-colors flex items-center gap-1"
              data-testid="research-shortcut-btn"
            >
              <Building2 className="w-3 h-3" />
              /research
            </button>
          </div>
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
              className="border-slate-600 bg-[#1E293B] hover:bg-[#334155] text-slate-300 shrink-0"
              title="Upload chart screenshot"
              data-testid="upload-image-btn"
            >
              <Image className="w-4 h-4" />
            </Button>
            <Input
              type="text"
              placeholder="Ask about stocks, or type /patterns"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && handleSend()}
              className="flex-1 bg-[#1E293B] border-slate-600 text-white placeholder-slate-500"
              data-testid="chat-input"
            />
            <Button
              onClick={handleSend}
              disabled={isLoading || (!input.trim() && !imageBase64)}
              className="bg-[#0052FF] hover:bg-[#2563EB] text-white"
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
