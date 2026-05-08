import React from 'react';

/**
 * PanelShell — renders children as a modal (if onClose is provided) or inline.
 * Used by workspace components to support both modal and tab-embedded modes.
 */
export default function PanelShell({ onClose, children, testId, maxWidth = 'max-w-2xl' }) {
  if (onClose) {
    return (
      <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4"
        data-testid={testId} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        <div className={`bg-slate-900 rounded-2xl ${maxWidth} w-full my-4 border border-slate-400/25`}>
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-slate-900/50 rounded-2xl w-full border border-slate-700/40" data-testid={testId}>
      {children}
    </div>
  );
}
