import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Minus, Brain, AlertTriangle, Target, Clock, RefreshCw } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const MarketPrediction = () => {
  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);

  useEffect(() => {
    fetchPrediction();
    // Auto-refresh every 5 minutes
    const interval = setInterval(fetchPrediction, 300000);
    return () => clearInterval(interval);
  }, []);

  const fetchPrediction = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api/market/prediction`);
      const data = await response.json();
      setPrediction(data);
      setLastUpdated(new Date());
    } catch (error) {
      console.error('Error fetching prediction:', error);
    } finally {
      setLoading(false);
    }
  };

  const getDirectionIcon = (direction) => {
    if (direction === 'BULLISH') return <TrendingUp className="w-6 h-6 text-green-400" />;
    if (direction === 'BEARISH') return <TrendingDown className="w-6 h-6 text-red-400" />;
    return <Minus className="w-6 h-6 text-yellow-400" />;
  };

  const getDirectionColor = (direction) => {
    if (direction === 'BULLISH') return 'text-green-400 bg-green-900 bg-opacity-20 border-green-800';
    if (direction === 'BEARISH') return 'text-red-400 bg-red-900 bg-opacity-20 border-red-800';
    return 'text-yellow-400 bg-yellow-900 bg-opacity-20 border-yellow-800';
  };

  const getConfidenceColor = (score) => {
    if (score >= 75) return 'text-green-400';
    if (score >= 50) return 'text-yellow-400';
    return 'text-orange-400';
  };

  if (loading && !prediction) {
    return (
      <div className="bg-[#0a0a0b] rounded-lg border border-gray-800 p-8">
        <div className="flex items-center justify-center gap-3">
          <RefreshCw className="w-6 h-6 text-blue-400 animate-spin" />
          <p className="text-white">Analyzing market data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-purple-600 rounded-lg flex items-center justify-center">
            <Brain className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-2xl font-bold">AI Market Prediction</h2>
            <p className="text-gray-400 text-sm">
              Powered by real-time scraping & GPT-5.2 analysis
            </p>
          </div>
        </div>
        <Button
          onClick={fetchPrediction}
          disabled={loading}
          variant="outline"
          className="bg-[#272729] border-gray-700 text-white hover:bg-[#2a2a2c]"
        >
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {prediction && (
        <>
          {/* Main Prediction Card */}
          <Card className={`bg-gradient-to-br from-purple-900 to-blue-900 border-2 ${
            prediction.overall_direction === 'BULLISH' ? 'border-green-500' :
            prediction.overall_direction === 'BEARISH' ? 'border-red-500' :
            'border-yellow-500'
          } p-6`}>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {/* Overall Direction */}
              <div className="text-center">
                <div className="flex items-center justify-center mb-2">
                  {getDirectionIcon(prediction.overall_direction)}
                </div>
                <h3 className="text-white text-2xl font-bold mb-1">
                  {prediction.overall_direction}
                </h3>
                <p className="text-gray-300 text-sm">Overall Direction</p>
              </div>

              {/* Confidence Score */}
              <div className="text-center">
                <div className={`text-4xl font-bold mb-2 ${getConfidenceColor(prediction.confidence_score)}`}>
                  {prediction.confidence_score}%
                </div>
                <p className="text-gray-300 text-sm">Confidence Score</p>
                <div className="mt-2 w-full bg-gray-800 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full ${
                      prediction.confidence_score >= 75 ? 'bg-green-500' :
                      prediction.confidence_score >= 50 ? 'bg-yellow-500' :
                      'bg-orange-500'
                    }`}
                    style={{ width: `${prediction.confidence_score}%` }}
                  />
                </div>
              </div>

              {/* Data Sources */}
              <div className="text-center">
                <div className="text-white text-sm space-y-1">
                  <div>📰 {prediction.data_sources?.news_articles || 0} News Articles</div>
                  <div>💬 {prediction.data_sources?.social_posts || 0} Social Posts</div>
                  <div>₿ {prediction.data_sources?.crypto_signals || 0} Crypto Signals</div>
                  <div>📊 {prediction.data_sources?.insider_trades || 0} Insider Trades</div>
                </div>
              </div>
            </div>

            {/* Summary */}
            {prediction.summary && (
              <div className="mt-6 pt-6 border-t border-gray-700">
                <p className="text-white text-center">{prediction.summary}</p>
              </div>
            )}
          </Card>

          {/* Timeframe Predictions */}
          {prediction.timeframes && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {['intraday', 'short_term', 'medium_term'].map((timeframe) => {
                const data = prediction.timeframes[timeframe];
                if (!data) return null;
                
                return (
                  <Card key={timeframe} className="bg-[#0a0a0b] border-gray-800 p-4">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <Clock className="w-4 h-4 text-blue-400" />
                        <h4 className="text-white font-semibold capitalize">
                          {timeframe.replace('_', ' ')}
                        </h4>
                      </div>
                      <Badge className={getDirectionColor(data.direction)}>
                        {data.direction}
                      </Badge>
                    </div>
                    {data.target && (
                      <div className="flex items-center gap-2 text-gray-300 text-sm">
                        <Target className="w-4 h-4" />
                        <span>Target: {data.target}</span>
                      </div>
                    )}
                  </Card>
                );
              })}
            </div>
          )}

          {/* Key Signals */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Bullish Signals */}
            {prediction.key_signals && prediction.key_signals.length > 0 && (
              <Card className="bg-[#0a0a0b] border-gray-800 p-5">
                <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-green-400" />
                  Key Signals
                </h3>
                <ul className="space-y-2">
                  {prediction.key_signals.map((signal, index) => (
                    <li key={index} className="text-gray-300 text-sm flex items-start gap-2">
                      <span className="text-green-400">•</span>
                      {signal}
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {/* Risk Factors */}
            {prediction.risk_factors && prediction.risk_factors.length > 0 && (
              <Card className="bg-[#0a0a0b] border-gray-800 p-5">
                <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                  <AlertTriangle className="w-5 h-5 text-orange-400" />
                  Risk Factors
                </h3>
                <ul className="space-y-2">
                  {prediction.risk_factors.map((risk, index) => (
                    <li key={index} className="text-gray-300 text-sm flex items-start gap-2">
                      <span className="text-orange-400">•</span>
                      {risk}
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          {/* Market Outlooks */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {prediction.stock_outlook && (
              <Card className="bg-[#0a0a0b] border-gray-800 p-5">
                <h3 className="text-white font-semibold mb-2">📈 Stock Market Outlook</h3>
                <p className="text-gray-300 text-sm">{prediction.stock_outlook}</p>
              </Card>
            )}
            {prediction.crypto_outlook && (
              <Card className="bg-[#0a0a0b] border-gray-800 p-5">
                <h3 className="text-white font-semibold mb-2">₿ Crypto Market Outlook</h3>
                <p className="text-gray-300 text-sm">{prediction.crypto_outlook}</p>
              </Card>
            )}
          </div>

          {/* Disclaimer */}
          <div className="bg-yellow-900 bg-opacity-20 border border-yellow-800 rounded-lg p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-yellow-200">
                <strong>Disclaimer:</strong> This prediction is generated by AI analyzing multiple data sources. 
                It should not be considered financial advice. Markets are unpredictable and past performance 
                doesn't guarantee future results. Always do your own research and consult with financial advisors.
              </div>
            </div>
          </div>

          {/* Last Updated */}
          {lastUpdated && (
            <p className="text-center text-gray-500 text-xs">
              Last updated: {lastUpdated.toLocaleString()}
            </p>
          )}
        </>
      )}
    </div>
  );
};

export default MarketPrediction;
