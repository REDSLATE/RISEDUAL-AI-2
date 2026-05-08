import React, { useState } from 'react';
import { Play, X } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

/* The OAuth-flow tutorial uploaded into the `tutorial` media category.
 * Hard-coded because there's only one of these and we don't want a
 * round-trip to look it up every render. If we add more tutorials,
 * promote this to /api/media/tutorial/<key>. */
const ALPACA_TUTORIAL_FILE_ID = '22457cb2-ae04-423a-ac04-db93c85186ca';

const BrokerTutorialVideo = ({
  brokerId = 'alpaca',
  variant = 'inline',           // 'inline' (BrokerConnect) | 'helpcenter'
  defaultOpen = false,
  fileId = ALPACA_TUTORIAL_FILE_ID,
  title = 'How to enter your account information',
}) => {
  const [open, setOpen] = useState(defaultOpen);

  // Currently the tutorial only covers Alpaca's OAuth flow. Other brokers
  // would need their own file_id — bail rather than show the wrong video.
  if (brokerId !== 'alpaca') return null;

  const src = `${API}/media/file/${fileId}`;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`w-full flex items-center gap-3 rounded-lg border border-[#3DE8D9]/30 bg-[#3DE8D9]/5 hover:bg-[#3DE8D9]/10 transition-colors px-3 py-2.5 ${
          variant === 'inline' ? 'mb-4' : 'mt-2'
        }`}
        data-testid={`broker-tutorial-open-${brokerId}`}
      >
        <span className="w-8 h-8 rounded-full bg-[#3DE8D9]/20 flex items-center justify-center shrink-0">
          <Play className="w-3.5 h-3.5 text-[#3DE8D9] ml-0.5" />
        </span>
        <span className="text-left flex-1">
          <span className="block text-white text-xs font-semibold">{title}</span>
          <span className="block text-slate-400 text-[10px]">
            Quick walkthrough · 1 min
          </span>
        </span>
        <span className="text-[10px] text-[#3DE8D9] font-medium">Watch</span>
      </button>
    );
  }

  return (
    <div
      className={`rounded-lg overflow-hidden border border-[#3DE8D9]/30 bg-slate-900/60 ${
        variant === 'inline' ? 'mb-4' : 'mt-2'
      }`}
      data-testid={`broker-tutorial-player-${brokerId}`}
    >
      <div className="flex items-center justify-between px-3 py-2 bg-slate-800/60 border-b border-slate-700/60">
        <span className="text-[11px] text-slate-300 font-medium">{title}</span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-slate-400 hover:text-white p-0.5"
          data-testid={`broker-tutorial-close-${brokerId}`}
          aria-label="Close tutorial video"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      <video
        src={src}
        controls
        autoPlay
        playsInline
        preload="auto"
        className="w-full aspect-video bg-black"
        data-testid={`broker-tutorial-video-${brokerId}`}
      />
    </div>
  );
};

export default BrokerTutorialVideo;
