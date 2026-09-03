/**
 * Global axios + fetch defaults for CSRF protection.
 *
 * The backend's ``CSRFHeaderMiddleware`` requires
 * ``X-Requested-With: XMLHttpRequest`` on every cookie-authenticated
 * state-mutating request (POST / PUT / PATCH / DELETE). A cross-site
 * attacker can't set this header without a CORS preflight — and our
 * CORS allowlist doesn't admit attacker origins — so this closes
 * the CSRF gap that came with SameSite=None httpOnly session cookies.
 *
 * We set the header on ALL requests (mutating and read-only) to
 * keep the wiring simple; the middleware ignores it on GETs.
 *
 * Import this once from the app entrypoint. Idempotent.
 */
import axios from 'axios';

let _installed = false;

export function installCSRFDefaults() {
  if (_installed) return;
  _installed = true;

  // 1) axios — the app's primary client. Set on defaults so every
  // axios call in the codebase inherits without changes.
  axios.defaults.headers.common['X-Requested-With'] = 'XMLHttpRequest';
  axios.defaults.withCredentials = true;

  // 2) fetch — wrap once so pockets of code using raw fetch still
  // get the header. We do NOT rewrite POST bodies, only headers.
  const origFetch = window.fetch.bind(window);
  window.fetch = function csrfFetch(input, init) {
    const opts = { ...(init || {}) };
    const headers = new Headers(opts.headers || (typeof input === 'object' ? input.headers : undefined));
    if (!headers.has('X-Requested-With')) {
      headers.set('X-Requested-With', 'XMLHttpRequest');
    }
    opts.headers = headers;
    // Preserve default of sending cookies same-origin. Callers can
    // still override with { credentials: 'omit' } explicitly.
    if (opts.credentials === undefined) {
      opts.credentials = 'include';
    }
    return origFetch(input, opts);
  };
}
