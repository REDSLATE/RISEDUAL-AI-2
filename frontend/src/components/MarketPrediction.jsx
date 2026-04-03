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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    if (direction === 'BULLISH') return <TrendingUp className="w-6 h-6 text-emerald-400" />;
    if (direction === 'BEARISH') return <TrendingDown className="w-6 h-6 text-red-400" />;
    return <Minus className="w-6 h-6 text-yellow-400" />;
  };

  const getDirectionColor = (direction) => {
    if (direction === 'BULLISH') return 'text-emerald-400 bg-emerald-900 bg-opacity-20 border-green-800';
    if (direction === 'BEARISH') return 'text-red-400 bg-red-900 bg-opacity-20 border-red-800';
    return 'text-yellow-400 bg-yellow-900 bg-opacity-20 border-yellow-800';
  };

  const getConfidenceColor = (score) => {
    if (score >= 75) return 'text-emerald-400';
    if (score >= 50) return 'text-yellow-400';
    return 'text-orange-400';
  };

  if (loading && !prediction) {
    return (
      <div className="bg-[#0F172A] rounded-xl border border-slate-700/50 p-8">
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
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-[#0052FF] rounded-xl flex items-center justify-center">
            <Brain className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold">AI Market Prediction</h2>
            <p className="text-slate-400 text-xs sm:text-sm">
              Powered by real-time scraping & GPT-5.2 analysis
            </p>
          </div>
        </div>
        <Button
          onClick={fetchPrediction}
          disabled={loading}
          variant="outline"
          className="bg-[#1E293B] border-slate-600 text-white hover:bg-slate-700"
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
                <p className="text-slate-300 text-sm">Overall Direction</p>
              </div>

              {/* Confidence Score */}
              <div className="text-center">
                <div className={`text-4xl font-bold mb-2 ${getConfidenceColor(prediction.confidence_score)}`}>
                  {prediction.confidence_score}%
                </div>
                <p className="text-slate-300 text-sm">Confidence Score</p>
                <div className="mt-2 w-full bg-slate-700 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full ${
                      prediction.confidence_score >= 75 ? 'bg-emerald-500' :
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
                  <div className="font-semibold mb-2">Data Sources:</div>
                  <div>📰 {prediction.data_sources?.news_articles || 0} Articles</div>
                  <div className="text-xs text-slate-400">
                    CNBC • Reuters • MarketWatch • Fox Business<br/>
                    WSJ • Bloomberg • OAN • Epoch Times
                  </div>
                  <div>💬 {prediction.data_sources?.social_posts || 0} Social Posts</div>
                  <div>₿ {prediction.data_sources?.crypto_signals || 0} Crypto Signals</div>
                  <div>📊 {prediction.data_sources?.insider_trades || 0} Insider Trades</div>
                  {prediction.data_sources?.world_events > 0 && (
                    <div>🌍 {prediction.data_sources.world_events} World Events</div>
                  )}
                  {prediction.data_sources?.foreign_markets > 0 && (
                    <div>📈 {prediction.data_sources.foreign_markets} Foreign Indices</div>
                  )}
                  {prediction.data_sources?.congressional_trades > 0 && (
                    <div>🏛 {prediction.data_sources.congressional_trades} Congressional Trades</div>
                  )}
                  {prediction.data_sources?.fed_announcements > 0 && (
                    <div>🏦 {prediction.data_sources.fed_announcements} Fed Announcements</div>
                  )}
                  {prediction.real_estate_summary && (
                    <div>🏠 {prediction.real_estate_summary?.data_sources || 0} Real Estate Sources</div>
                  )}
                </div>
              </div>
            </div>

            {/* Summary */}
            {prediction.summary && (
              <div className="mt-6 pt-6 border-t border-slate-600">
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
                  <Card key={timeframe} className="bg-[#0F172A] border-slate-700 p-4">
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
                      <div className="flex items-center gap-2 text-slate-300 text-sm">
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
              <Card className="bg-slate-800/50 border-slate-700/50 rounded-xl p-5">
                <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-emerald-400" />
                  Key Signals
                </h3>
                <ul className="space-y-2">
                  {prediction.key_signals.map((signal, index) => (
                    <li key={`signal-${index}-${signal.slice(0,20)}`} className="text-slate-300 text-sm flex items-start gap-2">
                      <span className="text-emerald-400">•</span>
                      {signal}
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {/* Risk Factors */}
            {prediction.risk_factors && prediction.risk_factors.length > 0 && (
              <Card className="bg-slate-800/50 border-slate-700/50 rounded-xl p-5">
                <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                  <AlertTriangle className="w-5 h-5 text-orange-400" />
                  Risk Factors
                </h3>
                <ul className="space-y-2">
                  {prediction.risk_factors.map((risk, index) => (
                    <li key={`risk-${index}-${risk.slice(0,20)}`} className="text-slate-300 text-sm flex items-start gap-2">
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
              <Card className="bg-slate-800/50 border-slate-700/50 rounded-xl p-5">
                <h3 className="text-white font-semibold mb-2">📈 Stock Market Outlook</h3>
                <p className="text-slate-300 text-sm">{prediction.stock_outlook}</p>
              </Card>
            )}
            {prediction.crypto_outlook && (
              <Card className="bg-slate-800/50 border-slate-700/50 rounded-xl p-5">
                <h3 className="text-white font-semibold mb-2">₿ Crypto Market Outlook</h3>
                <p className="text-slate-300 text-sm">{prediction.crypto_outlook}</p>
              </Card>
            )}
          </div>

          {/* Geopolitical Impact */}
          {prediction.geopolitical_impact && (
            <Card className="bg-gradient-to-r from-blue-900/40 to-indigo-900/40 border-blue-700/50 rounded-xl p-5">
              <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                🌍 Geopolitical Impact Assessment
              </h3>
              <p className="text-slate-300 text-sm">{prediction.geopolitical_impact}</p>
            </Card>
          )}

          {/* Macro Data Intelligence */}
          {prediction.macro_data && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* World Events */}
              {prediction.macro_data.world_events && prediction.macro_data.world_events.total > 0 && (
                <Card className="bg-slate-800/50 border-slate-700/50 rounded-xl p-5" data-testid="macro-world-events">
                  <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                    🌍 World Events
                  </h3>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Events Tracked</span>
                      <span className="text-white font-medium">{prediction.macro_data.world_events.total}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">High Impact</span>
                      <span className="text-red-400 font-medium">{prediction.macro_data.world_events.high_impact}</span>
                    </div>
                    {prediction.macro_data.world_events.top_sectors?.length > 0 && (
                      <div>
                        <span className="text-slate-400 text-xs">Affected Sectors:</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {prediction.macro_data.world_events.top_sectors.map((s) => (
                            <Badge key={s} variant="outline" className="text-xs border-slate-600 text-slate-300">{s}</Badge>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </Card>
              )}

              {/* Foreign Markets */}
              {prediction.macro_data.foreign_markets && prediction.macro_data.foreign_markets.total_indices > 0 && (
                <Card className="bg-slate-800/50 border-slate-700/50 rounded-xl p-5" data-testid="macro-foreign-markets">
                  <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                    📊 Foreign Markets
                  </h3>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Indices Tracked</span>
                      <span className="text-white font-medium">{prediction.macro_data.foreign_markets.total_indices}</span>
                    </div>
                    {prediction.macro_data.foreign_markets.correlation_signals?.length > 0 && (
                      <div>
                        <span className="text-slate-400 text-xs">Correlation Signals:</span>
                        <ul className="mt-1 space-y-1">
                          {prediction.macro_data.foreign_markets.correlation_signals.slice(0, 3).map((sig, i) => (
                            <li key={sig.signal || i} className="text-xs text-slate-300">
                              <span className={sig.change_percent > 0 ? 'text-emerald-400' : 'text-red-400'}>
                                {sig.change_percent > 0 ? '▲' : '▼'}
                              </span> {sig.signal}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </Card>
              )}

              {/* Government Data */}
              {prediction.macro_data.gov_filings && (
                <Card className="bg-slate-800/50 border-slate-700/50 rounded-xl p-5" data-testid="macro-gov-filings">
                  <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                    🏛 Government Data
                  </h3>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Congressional Trades</span>
                      <span className="text-white font-medium">{prediction.macro_data.gov_filings.congressional_trades}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Fed Announcements</span>
                      <span className="text-white font-medium">{prediction.macro_data.gov_filings.fed_announcements}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">SEC Insider Filings</span>
                      <span className="text-white font-medium">{prediction.macro_data.gov_filings.insider_trades}</span>
                    </div>
                  </div>
                </Card>
              )}
            </div>
          )}

          {/* Real Estate Impact */}
          {prediction.real_estate_summary && (
            <Card className="bg-gradient-to-r from-orange-900 to-red-900 bg-opacity-20 border-orange-800 p-5">
              <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                🏠 Real Estate Market Impact
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <p className="text-slate-400 text-sm mb-1">Housing Market Health:</p>
                  <p className="text-white font-medium capitalize">
                    {prediction.real_estate_summary.housing_health || 'Analyzing...'}
                  </p>
                </div>
                <div>
                  <p className="text-slate-400 text-sm mb-1">Commercial Trend:</p>
                  <p className="text-white font-medium capitalize">
                    {prediction.real_estate_summary.commercial_trend || 'Mixed'}
                  </p>
                </div>
              </div>
              {prediction.real_estate_summary.implications && (
                <div className="mt-4 pt-4 border-t border-orange-800">
                  <p className="text-slate-400 text-xs mb-2">Market Implications:</p>
                  <div className="space-y-1 text-sm">
                    {prediction.real_estate_summary.implications.stocks && (
                      <p className="text-slate-300">• Stocks: {prediction.real_estate_summary.implications.stocks}</p>
                    )}
                    {prediction.real_estate_summary.implications.crypto && (
                      <p className="text-slate-300">• Crypto: {prediction.real_estate_summary.implications.crypto}</p>
                    )}
                  </div>
                </div>
              )}
            </Card>
          )}

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
            <p className="text-center text-slate-500 text-xs">
              Last updated: {lastUpdated.toLocaleString()}
            </p>
          )}
        </>
      )}
    </div>
  );
};

export default MarketPrediction;
