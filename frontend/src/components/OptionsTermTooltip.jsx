import React from 'react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './ui/tooltip';

/**
 * OptionsTermTooltip — inline glossary hover for any options term.
 *
 * Wraps its children (typically a short label like "Delta" or "IV")
 * in a Shadcn tooltip. The tooltip body is sourced from the in-memory
 * glossary, which is fetched ONCE per session from
 * `GET /api/learn/options` and cached on `window.__optionsGlossary`.
 * If the term is unknown the children render plain — no crash, no
 * dead tooltip trigger.
 *
 * Usage:
 *   <OptionsTermTooltip termKey="delta">Δ</OptionsTermTooltip>
 *   <OptionsTermTooltip termKey="iv">IV</OptionsTermTooltip>
 */

let _glossaryPromise = null;

async function _loadGlossary() {
  if (typeof window === 'undefined') return {};
  if (window.__optionsGlossary) return window.__optionsGlossary;
  if (_glossaryPromise) return _glossaryPromise;

  const base = process.env.REACT_APP_BACKEND_URL || '';
  _glossaryPromise = fetch(`${base}/api/learn/options`)
    .then((r) => (r.ok ? r.json() : { terms: {} }))
    .then((body) => {
      window.__optionsGlossary = body.terms || {};
      return window.__optionsGlossary;
    })
    .catch(() => {
      window.__optionsGlossary = {};
      return {};
    });
  return _glossaryPromise;
}

export default function OptionsTermTooltip({ termKey, children, side = 'top' }) {
  const [doc, setDoc] = React.useState(null);

  React.useEffect(() => {
    let alive = true;
    _loadGlossary().then((glossary) => {
      if (!alive) return;
      const key = (termKey || '').toLowerCase();
      setDoc(glossary[key] || null);
    });
    return () => { alive = false; };
  }, [termKey]);

  // Unknown term — render the bare label, no tooltip trigger.
  if (!doc) {
    return (
      <span data-testid={`opt-term-${termKey}-bare`}>{children}</span>
    );
  }

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className="underline decoration-dotted decoration-slate-500 hover:decoration-violet-300 cursor-help"
            data-testid={`opt-term-${termKey}`}
          >
            {children}
          </span>
        </TooltipTrigger>
        <TooltipContent
          side={side}
          className="max-w-[280px] bg-slate-900 border-slate-700 text-slate-200 text-xs px-3 py-2"
        >
          <p className="font-semibold text-violet-300 mb-1">{doc.term}</p>
          <p>{doc.short}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export { OptionsTermTooltip };
