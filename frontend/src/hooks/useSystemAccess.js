import React from 'react';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Polls /api/system/access on mount and every POLL_INTERVAL_MS.
 *
 * Returns:
 *   { ready, publicAccess, isAdmin, message, refresh }
 *
 * - ready=false until the first poll returns. Use to gate the
 *   initial render so the SPA doesn't flash the dashboard before
 *   the lockout decision arrives.
 * - publicAccess=true means the site is open (no lockout).
 * - isAdmin=true means the cookie holder bypasses any lockout.
 * - message holds the operator-facing copy when the lockout is on
 *   (null otherwise).
 * - refresh() forces an immediate re-poll (e.g., after login).
 *
 * Defaults fail OPEN (publicAccess=true) so a network blip never
 * locks the user out of an otherwise-healthy site.
 */
const POLL_INTERVAL_MS = 30_000;

export default function useSystemAccess() {
  const [state, setState] = React.useState({
    ready: false,
    publicAccess: true,
    isAdmin: false,
    message: null,
  });

  const fetchOnce = React.useCallback(async () => {
    try {
      const res = await fetch(`${API}/system/access`, {
        credentials: 'include',
      });
      if (!res.ok) {
        // Anything other than 200 → fail open. The middleware
        // never returns non-200 on /api/system/access in the
        // shipped contract, so this only fires on transport errors.
        setState((s) => ({ ...s, ready: true }));
        return;
      }
      const data = await res.json();
      setState({
        ready: true,
        publicAccess: !!data.public_access,
        isAdmin: !!data.is_admin,
        message: data.message || null,
      });
    } catch {
      setState((s) => ({ ...s, ready: true }));
    }
  }, []);

  React.useEffect(() => {
    fetchOnce();
    const id = setInterval(fetchOnce, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchOnce]);

  return { ...state, refresh: fetchOnce };
}
