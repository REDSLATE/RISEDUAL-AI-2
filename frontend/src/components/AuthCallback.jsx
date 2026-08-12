import React, { useEffect, useRef, useState } from 'react';
import { toast } from './ui/sonner';
import { useAuth } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

/**
 * AuthCallback — exchanges an Emergent Auth session_id in the URL
 * fragment for our first-party JWT cookies, then lands the user on
 * the app shell.
 *
 * The parent decides when to render this (App.js checks
 * `window.location.hash.includes('session_id=')` synchronously
 * before AuthProvider's initial checkAuth fires — see AuthContext
 * for the matching "skip /me if hash has session_id" guard).
 */
const AuthCallback = () => {
  const hasProcessed = useRef(false);
  const { refreshAuth } = useAuth();
  const [state, setState] = useState('processing'); // processing | done | error

  useEffect(() => {
    // useRef + synchronous flag — prevents StrictMode double-run.
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const hash = window.location.hash || '';
    const m = /session_id=([^&]+)/.exec(hash);
    const sessionId = m ? decodeURIComponent(m[1]) : '';
    if (!sessionId) {
      setState('error');
      return;
    }

    (async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/auth/google/session`, {
          method: 'POST',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
            'X-Session-ID': sessionId,
          },
          body: JSON.stringify({ session_id: sessionId }),
        });
        if (!res.ok) {
          let detail = 'Google sign-in failed. Please try again.';
          try { detail = (await res.json()).detail || detail; } catch (_) { /* noop */ }
          throw new Error(detail);
        }
        const data = await res.json();
        // Strip the #session_id fragment so a reload doesn't re-fire.
        window.history.replaceState(
          null, '',
          window.location.pathname + window.location.search,
        );
        // AuthProvider is holding user=null (we told it to skip /me).
        // refreshAuth() re-fetches /auth/me via the cookie we just set.
        try {
          await refreshAuth();
        } catch (_) { /* noop */ }
        if (data.is_new_user) {
          toast.success(`Welcome, ${data.name || data.email}!`);
        } else {
          toast.success('Signed in with Google');
        }
        setState('done');
      } catch (e) {
        console.error('[AuthCallback] failed:', e);
        setState('error');
        toast.error(String(e.message || e));
        // Clean the hash so the user can retry from the landing page.
        setTimeout(() => {
          window.history.replaceState(
            null, '',
            window.location.pathname + window.location.search,
          );
          window.location.reload();
        }, 1500);
      }
    })();
  }, [refreshAuth]);

  return (
    <div
      data-testid="auth-callback"
      className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-200"
    >
      <div className="text-center">
        <div className="inline-block w-8 h-8 border-2 border-teal-400/60 border-t-teal-400 rounded-full animate-spin mb-4" />
        <div className="text-sm text-slate-400">
          {state === 'error'
            ? 'Sign-in failed — returning to the landing page…'
            : 'Signing you in…'}
        </div>
      </div>
    </div>
  );
};

export default AuthCallback;
