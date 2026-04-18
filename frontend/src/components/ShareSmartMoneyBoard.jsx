import React, { useRef, useState, useCallback } from 'react';
import { Share2, Download, Loader2, Check } from 'lucide-react';
import html2canvas from 'html2canvas';
import SparkLine from './SparkLine';
import { Button } from './ui/button';

/**
 * ShareSmartMoneyBoard — renders a shareable PNG of the user's top-5 watchlist
 * rows (ticker, SM score, sparkline, signal) with RiseDual branding.
 *
 * - Hidden styled card is captured via html2canvas
 * - Uses Web Share API when available (mobile); falls back to PNG download
 * - Purely client-side — no new backend calls
 */

const signalLabel = (signal) => {
  if (signal === 'bullish') return 'BULLISH';
  if (signal === 'bearish') return 'BEARISH';
  return 'NEUTRAL';
};

const signalColor = (score) => {
  if (score == null) return { fg: '#94a3b8', bg: '#1e293b', border: '#475569' };
  if (score >= 60) return { fg: '#34d399', bg: 'rgba(52,211,153,0.12)', border: 'rgba(52,211,153,0.35)' };
  if (score <= 40) return { fg: '#f87171', bg: 'rgba(248,113,113,0.12)', border: 'rgba(248,113,113,0.35)' };
  return { fg: '#fbbf24', bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.35)' };
};

export default function ShareSmartMoneyBoard({ watchlist = [], smartScores = {}, smsHistory = {} }) {
  const cardRef = useRef(null);
  const [state, setState] = useState('idle'); // idle | rendering | shared | error

  // Top 5 rows with a resolved SM score
  const rows = watchlist
    .filter(item => smartScores[item.symbol]?.score != null)
    .slice(0, 5);

  const handleShare = useCallback(async () => {
    if (!cardRef.current || rows.length === 0) return;
    setState('rendering');
    try {
      const canvas = await html2canvas(cardRef.current, {
        backgroundColor: '#050b1a',
        scale: 2,
        useCORS: true,
        logging: false,
      });
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('Failed to render image');

      const file = new File([blob], 'risedual-smart-money-board.png', { type: 'image/png' });
      const shareData = {
        files: [file],
        title: 'My Smart Money Board — RiseDual AI',
        text: 'Tracking institutional money flows with RiseDual AI. Where the smart money is pointing this quarter.',
      };

      // Try native Web Share API first (mobile + modern browsers)
      if (navigator.canShare?.(shareData) && navigator.share) {
        await navigator.share(shareData);
        setState('shared');
      } else {
        // Desktop fallback: trigger PNG download
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'risedual-smart-money-board.png';
        a.click();
        URL.revokeObjectURL(url);
        setState('shared');
      }
      setTimeout(() => setState('idle'), 2500);
    } catch (err) {
      if (err?.name === 'AbortError') {
        setState('idle');
        return;
      }
      // eslint-disable-next-line no-console
      console.error('Share board error:', err);
      setState('error');
      setTimeout(() => setState('idle'), 2500);
    }
  }, [rows]);

  if (rows.length === 0) return null;

  const canNativeShare = typeof navigator !== 'undefined'
    && typeof navigator.canShare === 'function'
    && typeof navigator.share === 'function';

  const btnLabel = state === 'rendering' ? 'Rendering…'
    : state === 'shared' ? (canNativeShare ? 'Shared' : 'Downloaded')
    : state === 'error' ? 'Retry'
    : (canNativeShare ? 'Share Board' : 'Download Board');

  const Icon = state === 'rendering' ? Loader2
    : state === 'shared' ? Check
    : canNativeShare ? Share2
    : Download;

  return (
    <>
      <Button
        onClick={handleShare}
        disabled={state === 'rendering'}
        variant="outline"
        size="sm"
        className="text-[11px] h-7 px-2 bg-slate-800/60 border-slate-600/40 text-slate-300 hover:text-white hover:bg-slate-700/60"
        data-testid="share-smart-money-board-btn"
      >
        <Icon className={`w-3 h-3 mr-1 ${state === 'rendering' ? 'animate-spin' : ''}`} />
        {btnLabel}
      </Button>

      {/* Hidden render target — off-screen but not display:none (html2canvas needs layout) */}
      <div className="absolute -left-[9999px] top-0 pointer-events-none" aria-hidden="true">
        <div
          ref={cardRef}
          style={{ width: 560, padding: 24, background: 'linear-gradient(135deg, #050b1a 0%, #0d1628 100%)', fontFamily: 'Inter, system-ui, sans-serif' }}
        >
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
            <div>
              <div style={{ color: '#94a3b8', fontSize: 11, textTransform: 'uppercase', letterSpacing: 2, marginBottom: 4 }}>
                Smart Money Board
              </div>
              <div style={{ color: '#fff', fontSize: 20, fontWeight: 700, letterSpacing: -0.3 }}>
                Where institutions are moving
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ color: '#3DE8D9', fontSize: 14, fontWeight: 700, letterSpacing: 0.5 }}>RISEDUAL AI</div>
              <div style={{ color: '#64748b', fontSize: 10 }}>
                {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
              </div>
            </div>
          </div>

          {/* Rows */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {rows.map((item) => {
              const sm = smartScores[item.symbol];
              const colors = signalColor(sm.score);
              const history = smsHistory[item.symbol] || [];
              return (
                <div key={item.symbol} style={{
                  display: 'flex', alignItems: 'center', gap: 14,
                  padding: '12px 16px', background: 'rgba(30,41,59,0.6)',
                  borderRadius: 10, border: '1px solid rgba(71,85,105,0.3)',
                }}>
                  <div style={{ color: '#fff', fontWeight: 700, fontSize: 15, width: 60 }}>{item.symbol}</div>
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                    minWidth: 48, padding: '4px 8px', borderRadius: 6, border: `1px solid ${colors.border}`,
                    background: colors.bg, color: colors.fg, fontWeight: 700, fontSize: 12, fontVariantNumeric: 'tabular-nums',
                  }}>
                    SM {sm.score}
                  </div>
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
                    {history.length >= 2 && <SparkLine points={history} width={100} height={22} />}
                  </div>
                  <div style={{ color: colors.fg, fontSize: 10, fontWeight: 700, letterSpacing: 1, textAlign: 'right' }}>
                    {signalLabel(sm.signal)}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Footer */}
          <div style={{ marginTop: 20, paddingTop: 14, borderTop: '1px solid rgba(71,85,105,0.3)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ color: '#64748b', fontSize: 10 }}>
              SM Score = institutional conviction from 13F filings (0 = bearish, 100 = bullish)
            </div>
            <div style={{ color: '#3DE8D9', fontSize: 10, fontWeight: 600 }}>
              risedual.ai
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
