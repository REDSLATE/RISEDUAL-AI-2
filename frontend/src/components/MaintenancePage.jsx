import React from 'react';

/**
 * MaintenancePage — full-screen "Technical Difficulties / Coming
 * Soon" view shown to public visitors while the ML stack is in its
 * organic data-collection window.
 *
 * Visual design notes:
 *   - Same dark palette (#060E1F) as the authenticated shell so
 *     returning users don't feel like they hit the wrong site.
 *   - Subtle pulsing dot signals "we're alive, just busy".
 *   - Owner sign-in entry point sits at the bottom — clicking it
 *     opens the existing AuthModal so the admin path is one click
 *     away. The middleware already lets /api/auth/* through, so
 *     the login form works even while the lockout is on.
 */
export default function MaintenancePage({ message, onAdminLogin }) {
  const copy = message ||
    "RISEDUAL is temporarily offline while the ML stack collects training data. We'll be back shortly.";

  return (
    <div
      data-testid="maintenance-page"
      className="min-h-screen bg-[#060E1F] text-slate-100 flex flex-col"
    >
      {/* Header strip */}
      <header className="px-8 py-6 border-b border-slate-800/60 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="text-lg tracking-[0.18em] font-semibold text-cyan-400">
            RISEDUAL
          </div>
          <span className="text-xs uppercase tracking-widest text-slate-500">
            AI Trading
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <span
            className="inline-block w-2 h-2 rounded-full bg-amber-400 animate-pulse"
            data-testid="maintenance-status-dot"
          />
          <span>System paused for ML training</span>
        </div>
      </header>

      {/* Centered hero */}
      <main className="flex-1 flex items-center justify-center px-6">
        <div className="max-w-xl w-full text-center">
          <div className="mb-6">
            <span className="inline-block px-3 py-1 text-[11px] uppercase tracking-widest rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">
              Technical Difficulties
            </span>
          </div>
          <h1
            className="text-4xl sm:text-5xl lg:text-6xl font-bold mb-6 leading-tight"
            data-testid="maintenance-headline"
          >
            We&apos;ll be back soon.
          </h1>
          <p
            className="text-base sm:text-lg text-slate-400 mb-10 leading-relaxed"
            data-testid="maintenance-message"
          >
            {copy}
          </p>
          <div className="text-sm text-slate-500 mb-10">
            Status:{' '}
            <span className="text-cyan-400 font-medium">
              ML stack collecting training data
            </span>
          </div>

          {/* Admin entry point */}
          <button
            type="button"
            data-testid="maintenance-admin-login"
            onClick={onAdminLogin}
            className="text-xs uppercase tracking-[0.2em] text-slate-500 hover:text-cyan-400 transition-colors duration-200 underline-offset-4 hover:underline"
          >
            Owner sign-in
          </button>
        </div>
      </main>

      <footer className="px-8 py-6 border-t border-slate-800/60 text-center text-xs text-slate-600">
        © 2026 RISEDUAL · This is not financial advice.
      </footer>
    </div>
  );
}
