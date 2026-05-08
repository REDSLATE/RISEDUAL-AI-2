import React, { useState, useEffect } from 'react';
import { HelpCircle, X } from 'lucide-react';

/**
 * IconTabBar — reusable icon-only tab strip with:
 *   - Native browser tooltip on hover ("translucent name") showing label + description
 *   - Active tab underline in configurable accent color
 *   - `ⓘ` Legend button at the right edge opening a popover listing every tab
 *   - Breadcrumb row under the tabs showing the active tab's name + description
 *   - Graceful fallback to labelled tabs when `enabled` is false
 *
 * @param {object} props
 * @param {Array}  props.tabs            – [{ key, label, icon, desc }]
 * @param {string} props.value           – currently active key
 * @param {(key:string)=>void} props.onChange
 * @param {boolean} props.enabled        – turns icon-only mode on/off (legacy if false)
 * @param {string}  [props.accent]       – tailwind color class (default #3DE8D9 teal)
 * @param {string}  [props.accentHex]    – hex for inline styles
 * @param {string}  [props.testIdPrefix] – per-tab data-testid prefix
 * @param {string}  [props.legendTitle]  – legend popover heading
 */
export default function IconTabBar({
  tabs, value, onChange,
  enabled = true,
  accent = 'text-[#3DE8D9]',
  accentHex = '#3DE8D9',
  testIdPrefix = 'tab',
  legendTitle = 'Icon Legend',
}) {
  const [legendOpen, setLegendOpen] = useState(false);
  const active = tabs.find(t => t.key === value) || tabs[0];

  useEffect(() => {
    if (!legendOpen) return;
    const esc = (e) => e.key === 'Escape' && setLegendOpen(false);
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [legendOpen]);

  return (
    <div className="mb-5">
      <div className="relative border-b border-slate-700/50">
        <div className={`flex items-center gap-0.5 overflow-x-auto pb-1 ${enabled ? 'pr-20' : ''} scrollbar-hide`}>
          {tabs.map(t => {
            const Icon = t.icon;
            const isActive = value === t.key;
            if (enabled) {
              return (
                <button
                  key={t.key}
                  onClick={() => onChange(t.key)}
                  title={`${t.label} — ${t.desc}`}
                  aria-label={t.label}
                  className={`relative flex items-center justify-center w-10 h-10 rounded-t-lg shrink-0 transition-all ${
                    isActive
                      ? `bg-slate-800 ${accent}`
                      : 'text-slate-400 hover:text-white hover:bg-slate-800/40'
                  }`}
                  style={isActive ? { borderBottom: `2px solid ${accentHex}` } : undefined}
                  data-testid={`${testIdPrefix}-${t.key}`}
                >
                  <Icon className="w-4 h-4" />
                </button>
              );
            }
            return (
              <button
                key={t.key}
                onClick={() => onChange(t.key)}
                className={`flex items-center gap-1.5 px-3 py-2 rounded-t-lg text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                  isActive
                    ? `bg-slate-800 ${accent} border-b-2`
                    : 'text-slate-400 hover:text-white'
                }`}
                style={isActive ? { borderColor: accentHex } : undefined}
                data-testid={`${testIdPrefix}-${t.key}`}
              >
                <Icon className="w-3.5 h-3.5" />
                {t.label}
              </button>
            );
          })}
        </div>

        {enabled && (
          <button
            onClick={() => setLegendOpen(v => !v)}
            className={`absolute right-14 top-1/2 -translate-y-1/2 flex items-center justify-center w-8 h-8 rounded-lg transition-colors ${
              legendOpen ? 'bg-slate-800 ' + accent : 'text-slate-500 hover:bg-slate-800/60'
            }`}
            title={legendTitle}
            aria-label="Open icon legend"
            data-testid={`${testIdPrefix}-legend-btn`}
          >
            <HelpCircle className="w-4 h-4" />
          </button>
        )}

        {legendOpen && (
          <>
            <div className="fixed inset-0 z-[50]" onClick={() => setLegendOpen(false)} />
            <div
              className="absolute right-0 top-full mt-2 z-[60] w-[min(420px,calc(100vw-24px))] rounded-xl border border-slate-700/60 bg-[#0b1426] shadow-2xl shadow-black/60 overflow-hidden animate-enter"
              data-testid={`${testIdPrefix}-legend`}
            >
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-700/50 bg-slate-900/60">
                <div className="flex items-center gap-2">
                  <HelpCircle className={`w-3.5 h-3.5 ${accent}`} />
                  <span className="text-white text-xs font-semibold tracking-wide uppercase">{legendTitle}</span>
                </div>
                <button onClick={() => setLegendOpen(false)} className="text-slate-400 hover:text-white p-1 rounded" aria-label="Close legend">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="max-h-[70vh] overflow-y-auto py-1">
                {tabs.map(t => {
                  const Icon = t.icon;
                  return (
                    <div key={t.key} className="flex items-start gap-3 px-4 py-2 hover:bg-slate-800/60">
                      <div className="w-7 h-7 rounded-md bg-slate-800 border border-slate-700/60 flex items-center justify-center shrink-0 mt-0.5">
                        <Icon className={`w-3.5 h-3.5 ${accent}`} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="text-white text-[12px] font-semibold leading-tight">{t.label}</div>
                        <div className="text-slate-400 text-[11px] leading-snug">{t.desc}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>

      {enabled && active && (
        <div className="mt-2 flex items-center gap-2">
          <active.icon className={`w-3.5 h-3.5 ${accent}`} />
          <span className="text-white text-sm font-semibold">{active.label}</span>
          <span className="text-slate-500 text-[11px] hidden sm:inline">· {active.desc}</span>
        </div>
      )}
    </div>
  );
}
