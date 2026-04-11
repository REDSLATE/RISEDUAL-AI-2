import React, { useState, useEffect } from 'react';
import { CheckCircle, AlertCircle, FileCode, Download, Loader2, RefreshCw } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

const AdminTools = () => {
  const [downloading, setDownloading] = useState(false);
  const [codeQuality, setCodeQuality] = useState(null);
  const [loadingQuality, setLoadingQuality] = useState(true);

  useEffect(() => {
    const fetchQuality = async () => {
      try {
        const res = await authFetch(`${API}/admin/code-quality`);
        if (res.ok) setCodeQuality(await res.json());
      } catch (e) { logger.error('Code quality fetch error:', e); }
      finally { setLoadingQuality(false); }
    };
    fetchQuality();
  }, []);

  const downloadCodebase = async () => {
    setDownloading(true);
    try {
      const res = await fetch(`${API}/download/codebase-pdf`);
      if (!res.ok) throw new Error('Download failed');
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'RISEDUAL_AI_Complete_Codebase.pdf';
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      a.remove();
    } catch (e) {
      logger.error('Download error:', e);
      alert('Failed to download. Please try again.');
    } finally {
      setDownloading(false);
    }
  };

  const gradeColor = (grade) => {
    if (grade?.startsWith('A')) return 'text-lime-400 bg-lime-700 border-emerald-700/50';
    if (grade?.startsWith('B')) return 'text-blue-400 bg-blue-900/30 border-blue-700/50';
    if (grade?.startsWith('C')) return 'text-amber-300 bg-amber-900/30 border-amber-700/50';
    return 'text-orange-400 bg-orange-800 border-red-700/50';
  };

  return (
    <div className="p-6 space-y-6" data-testid="admin-tools">
      <h3 className="text-white text-sm font-semibold">Developer Tools</h3>

      <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-5" data-testid="code-quality-card">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-xl bg-green-600 border border-emerald-700/30 flex items-center justify-center shrink-0">
            <CheckCircle className="w-6 h-6 text-lime-400" />
          </div>
          <div className="flex-1">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-white text-sm font-semibold">Code Quality Score</h4>
              {loadingQuality ? (
                <RefreshCw className="w-4 h-4 text-slate-400 animate-spin" />
              ) : codeQuality ? (
                <div className="flex items-center gap-2">
                  <span className={`text-2xl font-black ${gradeColor(codeQuality.grade).split(' ')[0]}`}>{codeQuality.score}</span>
                  <Badge className={`text-sm font-bold px-2.5 py-1 rounded-lg border ${gradeColor(codeQuality.grade)}`} data-testid="code-quality-grade">
                    {codeQuality.grade}
                  </Badge>
                </div>
              ) : (
                <span className="text-slate-300 text-xs">Unavailable</span>
              )}
            </div>

            {codeQuality && (
              <>
                <div className="w-full bg-slate-700/50 rounded-full h-2.5 mb-4">
                  <div className="h-2.5 rounded-full transition-all bg-gradient-to-r from-emerald-500 to-cyan-400" style={{ width: `${codeQuality.score}%` }} />
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-4">
                  {Object.entries(codeQuality.breakdown).map(([key, item]) => (
                    <div key={key} className="bg-slate-800/50 rounded-lg p-2.5 text-center">
                      <div className="flex items-center justify-center gap-1 mb-0.5">
                        {item.score >= item.max * 0.7 ? (
                          <CheckCircle className="w-3 h-3 text-lime-400" />
                        ) : (
                          <AlertCircle className="w-3 h-3 text-amber-300" />
                        )}
                        <span className="text-white text-xs font-bold">{item.score}/{item.max}</span>
                      </div>
                      <span className="text-slate-400 text-[9px]">{item.label}</span>
                    </div>
                  ))}
                </div>
                <div className="flex flex-wrap gap-3 text-[10px] text-slate-400">
                  <span>{codeQuality.metrics.backend_files} backend files ({codeQuality.metrics.backend_lines} lines)</span>
                  <span>{codeQuality.metrics.frontend_files} frontend files ({codeQuality.metrics.frontend_lines} lines)</span>
                  <span>{codeQuality.metrics.test_files} tests</span>
                  <span>{codeQuality.metrics.service_modules} services</span>
                  <span>{codeQuality.metrics.route_modules} routes</span>
                  <span>{codeQuality.metrics.components} components</span>
                </div>
              </>
            )}
          </div>
        </div>
      </Card>

      <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-5">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-xl bg-[#3DE8D9]/10 border border-[#3DE8D9]/20 flex items-center justify-center shrink-0">
            <FileCode className="w-6 h-6 text-[#3DE8D9]" />
          </div>
          <div className="flex-1 min-w-0">
            <h4 className="text-white text-sm font-semibold mb-1">Download Complete Source Code</h4>
            <p className="text-slate-300 text-xs leading-relaxed mb-3">
              Export the entire RISEDUAL AI codebase as a 305-page PDF. Includes all source code, architecture documentation, database schemas, API reference, environment configuration, setup guide, and the full test suite.
            </p>
            <div className="flex flex-wrap gap-2 mb-4">
              {['Frontend', 'Backend', 'Services', 'Routes', 'Models', 'Tests', 'Config', 'API Docs', 'DB Schemas'].map(tag => (
                <span key={tag} className="text-[9px] px-2 py-0.5 rounded-full bg-slate-700/60 text-slate-300 border border-slate-600/40">{tag}</span>
              ))}
            </div>
            <Button
              onClick={downloadCodebase}
              disabled={downloading}
              className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white text-xs h-9 px-4 rounded-xl transition-all"
              data-testid="download-codebase-btn"
            >
              {downloading ? (
                <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Downloading...</>
              ) : (
                <><Download className="w-4 h-4 mr-2" /> Download PDF (0.7 MB)</>
              )}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
};

export default AdminTools;
