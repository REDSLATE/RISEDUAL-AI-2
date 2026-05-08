/**
 * useReferralCapture — one-shot referral tracking hook.
 *
 * On app mount: checks URL for ``?ref=share-*``. If present and we haven't
 * already logged this ref in the current session, POSTs it to
 * /api/analytics/ref and stores the flag in sessionStorage so a route change
 * doesn't re-log.
 *
 * Safe to call at the top of <App/>. Zero dependencies on auth — anonymous
 * landings are tracked too (that's the whole point of referral attribution).
 */
import { useEffect } from 'react';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';

const SESSION_KEY = 'risedual-ref-logged';
const API = `${getApiBase()}/api/analytics/ref`;

export default function useReferralCapture() {
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const ref = params.get('ref');
      if (!ref || !ref.startsWith('share-')) return;

      // Dedupe — only log once per session per ref
      const logged = sessionStorage.getItem(SESSION_KEY);
      if (logged === ref) return;

      fetch(API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ ref, path: window.location.pathname || '/' }),
        keepalive: true,
      }).catch((err) => logger.warn('referral capture fetch failed', err));

      sessionStorage.setItem(SESSION_KEY, ref);
    } catch (err) {
      // Expected: SSR (no window) or privacy-mode browsers that throw
      // on sessionStorage access. Dev-only log so we can diagnose if a
      // real regression sneaks in.
      logger.warn('referral capture skipped', err);
    }
  }, []);
}
