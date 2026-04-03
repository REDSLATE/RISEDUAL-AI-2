import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AuthContext = createContext(null);

export const useAuth = () => useContext(AuthContext);

// Helper to make authenticated requests with retry
export const authFetch = async (url, options = {}, retries = 2) => {
  const token = localStorage.getItem('access_token');
  const headers = { ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  
  for (let i = 0; i <= retries; i++) {
    try {
      const res = await fetch(url, { ...options, headers });
      if (res.status === 502 && i < retries) {
        await new Promise(r => setTimeout(r, 1000 * (i + 1)));
        continue;
      }
      return res;
    } catch (e) {
      if (i === retries) throw e;
      await new Promise(r => setTimeout(r, 1000 * (i + 1)));
    }
  }
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const checkAuth = useCallback(async () => {
    const token = localStorage.getItem('access_token');
    if (!token) { setUser(false); setLoading(false); return; }
    try {
      const res = await authFetch(`${API}/auth/me`);
      if (res.ok) {
        setUser(await res.json());
      } else {
        // Try refresh
        const refreshed = await tryRefresh();
        if (!refreshed) { clearTokens(); setUser(false); }
      }
    } catch {
      setUser(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { checkAuth(); }, [checkAuth]);

  const tryRefresh = async () => {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return false;
    try {
      const res = await fetch(`${API}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (res.ok) {
        const data = await res.json();
        localStorage.setItem('access_token', data.access_token);
        const meRes = await authFetch(`${API}/auth/me`);
        if (meRes.ok) { setUser(await meRes.json()); return true; }
      }
    } catch {}
    return false;
  };

  const storeTokens = (data) => {
    if (data.access_token) localStorage.setItem('access_token', data.access_token);
    if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
  };

  const clearTokens = () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  };

  const fetchWithRetry = async (url, opts, retries = 2) => {
    for (let i = 0; i <= retries; i++) {
      try {
        const res = await fetch(url, opts);
        if (res.status === 502 && i < retries) {
          await new Promise(r => setTimeout(r, 1000 * (i + 1)));
          continue;
        }
        return res;
      } catch (e) {
        if (i === retries) throw e;
        await new Promise(r => setTimeout(r, 1000 * (i + 1)));
      }
    }
  };

  const login = async (email, password) => {
    const res = await fetchWithRetry(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(formatDetail(data.detail));
    storeTokens(data);
    setUser(data);
    return data;
  };

  const register = async (email, password, name) => {
    const res = await fetchWithRetry(`${API}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, name }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(formatDetail(data.detail));
    storeTokens(data);
    setUser(data);
    return data;
  };

  const logout = async () => {
    await fetch(`${API}/auth/logout`, { method: 'POST' }).catch(() => {});
    clearTokens();
    setUser(false);
  };

  const isPro = user && user.subscription_status === 'pro';

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, isPro, checkAuth, authFetch }}>
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
