import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AuthContext = createContext(null);

export const useAuth = () => useContext(AuthContext);

// Auth uses httpOnly cookies set by the server.
// credentials: 'include' ensures cookies are sent with every request.
// NOTE: AbortController.signal removed — causes "postMessage clone" errors
//       with service workers and deployment proxies. Using Promise.race for timeout.
export const authFetch = async (url, options = {}, retries = 3) => {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  for (let i = 0; i <= retries; i++) {
    try {
      const res = await Promise.race([
        fetch(url, { ...options, headers, credentials: 'include' }),
        new Promise((_, reject) => setTimeout(() => reject(new Error('Request timeout')), 30000)),
      ]);
      if (res.status === 502 && i < retries) {
        await new Promise(r => setTimeout(r, 1500 * (i + 1)));
        continue;
      }
      return res;
    } catch (e) {
      if (i === retries) throw e;
      await new Promise(r => setTimeout(r, 1500 * (i + 1)));
    }
  }
};

// Plain fetch with retry and credentials
const fetchWithRetry = async (url, opts, retries = 3) => {
  for (let i = 0; i <= retries; i++) {
    try {
      const res = await Promise.race([
        fetch(url, { ...opts, credentials: 'include' }),
        new Promise((_, reject) => setTimeout(() => reject(new Error('Request timeout')), 30000)),
      ]);
      if (res.status === 502 && i < retries) {
        await new Promise(r => setTimeout(r, 1500 * (i + 1)));
        continue;
      }
      return res;
    } catch (e) {
      if (i === retries) throw new Error('Network error. Please check your connection and try again.');
      await new Promise(r => setTimeout(r, 1500 * (i + 1)));
    }
  }
};

// Dev-only logger (mirrors utils/logger.js for context module)
const isDev = process.env.NODE_ENV === 'development';
const log = {
  warn: (...args) => isDev && console.warn(...args),
  error: (...args) => isDev && console.error(...args),
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const clearLegacyTokens = useCallback(() => {
    // One-time cleanup of any legacy localStorage tokens from pre-cookie migration
    try {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
    } catch {
      // localStorage may be unavailable (private browsing)
    }
  }, []);

  const tryRefresh = useCallback(async () => {
    try {
      const res = await fetch(`${API}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({}),
      });
      if (res.ok) {
        const meRes = await authFetch(`${API}/auth/me`);
        if (meRes.ok) { setUser(await meRes.json()); return true; }
      }
    } catch (e) {
      log.warn('Token refresh failed:', e.message);
    }
    return false;
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/auth/me`);
      if (res.ok) {
        setUser(await res.json());
      } else if (res.status === 401) {
        const refreshed = await tryRefresh();
        if (!refreshed) { clearLegacyTokens(); setUser(false); }
      } else {
        setUser(false);
      }
    } catch (e) {
      log.warn('Auth check failed:', e.message);
      setUser(false);
    } finally {
      setLoading(false);
    }
  }, [tryRefresh, clearLegacyTokens]);

  useEffect(() => { checkAuth(); }, [checkAuth]);

  const login = useCallback(async (email, password) => {
    const res = await fetchWithRetry(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      let detail;
      try { detail = (await res.json()).detail; } catch { detail = `Server error (${res.status}). Please try again.`; }
      throw new Error(formatDetail(detail));
    }
    const data = await res.json();
    clearLegacyTokens();
    setUser(data);
    return data;
  }, [clearLegacyTokens]);

  const register = useCallback(async (email, password, name, refCode) => {
    const body = { email, password, name };
    if (refCode) body.ref_code = refCode;
    const res = await fetchWithRetry(`${API}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail;
      try { detail = (await res.json()).detail; } catch { detail = `Server error (${res.status}). Please try again.`; }
      throw new Error(formatDetail(detail));
    }
    const data = await res.json();
    clearLegacyTokens();
    setUser(data);
    return data;
  }, [clearLegacyTokens]);

  const logout = useCallback(async () => {
    try {
      await fetch(`${API}/auth/logout`, { method: 'POST', credentials: 'include' });
    } catch (e) {
      log.warn('Logout request failed:', e.message);
    }
    clearLegacyTokens();
    setUser(false);
  }, [clearLegacyTokens]);

  const isPro = user && (user.subscription_status === 'pro' || user.subscription_status === 'trial');

  const contextValue = useMemo(() => ({
    user, loading, login, register, logout, isPro, checkAuth, authFetch
  }), [user, loading, login, register, logout, isPro, checkAuth]);

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
};

function formatDetail(detail) {
  if (detail == null) return 'Something went wrong. Please try again.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => e?.msg || JSON.stringify(e)).join(' ');
  if (detail?.msg) return detail.msg;
  return String(detail);
}
