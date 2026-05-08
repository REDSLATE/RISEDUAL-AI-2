import React, { useState, useEffect } from 'react';
import { Bitcoin, BarChart3, LayoutGrid } from 'lucide-react';
import CryptoTicker from './CryptoTicker';
import SectorHeatmap from './SectorHeatmap';

const STORAGE_KEY = 'risedual:markets-view';
const DEFAULT_MODE = 'both';
const MODES = [
  { key: 'both',    label: 'Both',    icon: LayoutGrid },
  { key: 'crypto',  label: 'Crypto',  icon: Bitcoin },
  { key: 'sectors', label: 'Sectors', icon: BarChart3 },
];

// Dashboard section that hosts the Crypto + Sector heatmaps together with a
// density-friendly toggle so users can collapse either one when the dashboard
// feels tall. Persisted in localStorage.
export default function MarketsSection() {
  const [mode, setMode] = useState(() => {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return (v && MODES.some((m) => m.key === v)) ? v : DEFAULT_MODE;
    } catch {
      return DEFAULT_MODE;
    }
  });

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, mode); } catch { /* ignore */ }
  }, [mode]);

  const showCrypto  = mode === 'both' || mode === 'crypto';
  const showSectors = mode === 'both' || mode === 'sectors';

  return (
    <div data-testid="markets-section" className="space-y-4">
      {/* Toggle — segmented pill group */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">Markets</span>
        <div className="inline-flex rounded-full border border-slate-700/60 bg-slate-900/40 p-0.5" role="tablist">
          {MODES.map((m) => {
            const Icon = m.icon;
            const active = mode === m.key;
            return (
              <button
                key={m.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setMode(m.key)}
                className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-semibold transition-colors ${
                  active
                    ? 'bg-[#3DE8D9]/15 text-[#3DE8D9] shadow-[inset_0_0_0_1px_rgba(61,232,217,0.4)]'
                    : 'text-slate-400 hover:text-white'
                }`}
                data-testid={`markets-toggle-${m.key}`}
              >
                <Icon className="w-3 h-3" />
                {m.label}
              </button>
            );
          })}
        </div>
      </div>

      {showCrypto  && <CryptoTicker />}
      {showSectors && <SectorHeatmap />}
    </div>
  );
}
