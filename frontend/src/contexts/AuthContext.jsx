import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AuthContext = createContext(null);

export const useAuth = () => useContext(AuthContext);

// Auth uses httpOnly cookies set by the server.
// credentials: 'include' ensures cookies are sent with every request.
export const authFetch = async (url, options = {}, retries = 3) => {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  for (let i = 0; i <= retries; i++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    try {
      const res = await fetch(url, { ...options, headers, credentials: 'include', signal: controller.signal });
      clearTimeout(timeoutId);
      if (res.status === 502 && i < retries) {
        await new Promise(r => setTimeout(r, 1500 * (i + 1)));
        continue;
      }
      return res;
    } catch (e) {
      clearTimeout(timeoutId);
      if (i === retries) throw e;
      await new Promise(r => setTimeout(r, 1500 * (i + 1)));
    }
  }
};

// Plain fetch with retry and credentials
const fetchWithRetry = async (url, opts, retries = 3) => {
  for (let i = 0; i <= retries; i++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    try {
      const res = await fetch(url, { ...opts, credentials: 'include', signal: controller.signal });
      clearTimeout(timeoutId);
      if (res.status === 502 && i < retries) {
        await new Promise(r => setTimeout(r, 1500 * (i + 1)));
        continue;
      }
      return res;
    } catch (e) {
      clearTimeout(timeoutId);
      if (i === retries) throw new Error('Network error. Please check your connection and try again.');
      await new Promise(r => setTimeout(r, 1500 * (i + 1)));
    }
  }
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const clearLegacyTokens = useCallback(() => {
    // One-time cleanup of any legacy localStorage tokens from pre-cookie migration
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
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
    } catch {
      // Refresh failed silently
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
    } catch {
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
    clearLegacyTokens(); // Ensure no stale localStorage tokens
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
    await fetch(`${API}/auth/logout`, { method: 'POST', credentials: 'include' }).catch(() => {});
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
