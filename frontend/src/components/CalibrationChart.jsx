import React, { useState, useEffect, useCallback } from 'react';
import { Activity, RefreshCw, AlertCircle } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import logger from '../utils/logger';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Cell
} from 'recharts';

const API = `${getApiBase()}/api`;

const CalibrationChart = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/ml/calibration-curve`);
      if (res.ok) setData(await res.json());
    } catch (e) {
      logger.warn('Calibration curve fetch failed:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <Card className="p-4 border border-slate-700/40 bg-slate-800/20" data-testid="calibration-loading">
        <div className="flex items-center justify-center py-6">
          <RefreshCw className="w-4 h-4 text-violet-400 animate-spin" />
          <span className="text-slate-400 text-xs ml-2">Loading calibration data...</span>
        </div>
      </Card>
    );
  }

  if (!data || data.status === 'no_model') {
    return (
      <Card className="p-5 border border-slate-700/40 bg-slate-800/20" data-testid="calibration-no-model">
        <div className="flex items-center gap-2 mb-3">
          <Activity className="w-4 h-4 text-violet-400" />
          <span className="text-white text-sm font-semibold">Calibration Curve</span>
        </div>
        <div className="text-center py-6">
          <AlertCircle className="w-8 h-8 text-slate-600 mx-auto mb-2" />
          <p className="text-slate-400 text-sm">Awaiting trained model</p>
          <p className="text-slate-500 text-[10px] mt-1 max-w-xs mx-auto">
            {data?.message || 'Collect 100+ labeled snapshots and train the signal model to see calibration metrics'}
          </p>
        </div>
      </Card>
    );
  }

  const curveData = data.curve_data || [];
  const summary = data.summary || {};

  // Transform for recharts — show predicted vs actual side by side
  const chartData = curveData.map(d => ({
    bucket: d.confidence_bucket,
    predicted: d.mean_predicted,
    actual: d.actual_accuracy,
    count: d.count,
    gap: Math.abs(d.mean_predicted - d.actual_accuracy),
  }));

  return (
    <Card className="p-4 border border-slate-700/40 bg-slate-800/20" data-testid="calibration-chart">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-violet-400" />
          <span className="text-white text-sm font-semibold">Calibration Curve</span>
          <Badge className="bg-violet-500/15 text-violet-400 border-0 text-[9px]">
            v{summary.model_version || '?'}
          </Badge>
        </div>
        <Button size="sm" variant="outline" onClick={fetchData}
          className="bg-slate-800 border-slate-600 text-slate-300 text-xs h-7" data-testid="calibration-refresh">
          <RefreshCw className="w-3 h-3 mr-1" /> Refresh
        </Button>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-4 gap-2 mb-4">
        <div className="text-center py-2 bg-slate-900/40 rounded-lg">
          <p className="text-white text-sm font-bold">{((summary.accuracy || 0) * 100).toFixed(1)}%</p>
          <p className="text-slate-500 text-[9px]">Accuracy</p>
        </div>
        <div className="text-center py-2 bg-slate-900/40 rounded-lg">
          <p className="text-amber-400 text-sm font-bold">{(summary.brier_score || 0).toFixed(4)}</p>
          <p className="text-slate-500 text-[9px]">Brier Score</p>
        </div>
        <div className="text-center py-2 bg-slate-900/40 rounded-lg">
          <p className={`text-sm font-bold ${(summary.ece || 0) < 0.1 ? 'text-emerald-400' : 'text-amber-400'}`}>
            {(summary.ece || 0).toFixed(4)}
          </p>
          <p className="text-slate-500 text-[9px]">ECE</p>
        </div>
        <div className="text-center py-2 bg-slate-900/40 rounded-lg">
          <p className="text-white text-sm font-bold">{summary.n_predictions || 0}</p>
          <p className="text-slate-500 text-[9px]">Predictions</p>
        </div>
      </div>

      {/* Chart */}
      {chartData.length > 0 && (
        <div className="h-52">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} barGap={2}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis
                dataKey="bucket"
                tick={{ fill: '#64748b', fontSize: 9 }}
                angle={-20}
                textAnchor="end"
                height={40}
              />
              <YAxis
                tick={{ fill: '#64748b', fontSize: 9 }}
                domain={[0, 1]}
                tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
              />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: '8px' }}
                labelStyle={{ color: '#94a3b8', fontSize: 10 }}
                formatter={(value, name) => [
                  `${(value * 100).toFixed(1)}%`,
                  name === 'predicted' ? 'Predicted' : 'Actual'
                ]}
              />
              <ReferenceLine
                y={0}
                stroke="#334155"
              />
              {/* Diagonal reference: perfect calibration would have predicted == actual */}
              <Bar dataKey="predicted" name="predicted" fill="#8b5cf6" radius={[3, 3, 0, 0]} opacity={0.6} />
              <Bar dataKey="actual" name="actual" radius={[3, 3, 0, 0]}>
                {chartData.map((entry, idx) => (
                  <Cell
                    key={`cell-${entry.bin || idx}`}
                    fill={entry.actual >= entry.predicted ? '#3DE8D9' : '#f97316'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center justify-center gap-6 mt-3">
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-violet-500/60" />
          <span className="text-slate-400 text-[10px]">Predicted Confidence</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-teal-400" />
          <span className="text-slate-400 text-[10px]">Actual Accuracy</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-orange-400" />
          <span className="text-slate-400 text-[10px]">Under-calibrated</span>
        </div>
      </div>

      {/* Interpretation */}
      <p className="text-slate-600 text-[9px] text-center mt-2">
        Bars should be equal height when perfectly calibrated. Green = actual meets or exceeds prediction. Orange = overconfident.
      </p>
    </Card>
  );
};

export default CalibrationChart;
