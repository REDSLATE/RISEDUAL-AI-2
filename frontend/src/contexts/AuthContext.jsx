import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AuthContext = createContext(null);

export const useAuth = () => useContext(AuthContext);

// Auth uses httpOnly cookies set by the server.
// credentials: 'include' ensures cookies are sent with every request.
// Falls back to Bearer token from localStorage for backward compatibility during migration.
export const authFetch = async (url, options = {}, retries = 3) => {
  const headers = { ...options.headers };
  // Fallback: if a localStorage token exists (legacy), send it as Bearer
  const token = localStorage.getItem('access_token');
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  
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

// Plain fetch with retry and credentials — module-level, no React state dependency
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

  const clearTokens = useCallback(() => {
    // Clear legacy localStorage tokens
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  }, []);

  const tryRefresh = useCallback(async () => {
    try {
      // httpOnly cookies are sent automatically with credentials: 'include'
      const res = await fetch(`${API}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({}),
      });
      if (res.ok) {
        const data = await res.json();
        // Store in localStorage as fallback for any components using Bearer directly
        if (data.access_token) localStorage.setItem('access_token', data.access_token);
        const meRes = await authFetch(`${API}/auth/me`);
        if (meRes.ok) { setUser(await meRes.json()); return true; }
      }
    } catch (e) {
      console.error('Token refresh failed:', e);
    }
    return false;
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      // Try authenticating via httpOnly cookies (sent automatically)
      const res = await authFetch(`${API}/auth/me`);
      if (res.ok) {
        setUser(await res.json());
      } else if (res.status === 401) {
        // Try refresh (cookie-based)
        const refreshed = await tryRefresh();
        if (!refreshed) { clearTokens(); setUser(false); }
      } else {
        setUser(false);
      }
    } catch {
      setUser(false);
    } finally {
      setLoading(false);
    }
  }, [tryRefresh, clearTokens]);

  useEffect(() => { checkAuth(); }, [checkAuth]);

  const storeTokens = useCallback((data) => {
    // Store in localStorage as fallback (Bearer header for any legacy code paths)
    if (data.access_token) localStorage.setItem('access_token', data.access_token);
    if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
  }, []);

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
    storeTokens(data); // localStorage fallback; primary auth is via httpOnly cookies
    setUser(data);
    return data;
  }, [storeTokens]);

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
    storeTokens(data); // localStorage fallback
    setUser(data);
    return data;
  }, [storeTokens]);

  const logout = useCallback(async () => {
    await fetch(`${API}/auth/logout`, { method: 'POST', credentials: 'include' }).catch(() => {});
    clearTokens();
    setUser(false);
  }, [clearTokens]);

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
