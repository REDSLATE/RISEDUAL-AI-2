// Tracks the last N tickers the user ran through any War Room subtab.
// Persisted in localStorage so the "Recent" strip survives reloads.
// Any subscriber receives the updated list whenever addRecent is called.

const KEY = 'risedual:recent-tickers';
const MAX = 3;
const listeners = new Set();

const read = () => {
  try {
    const raw = localStorage.getItem(KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((t) => typeof t === 'string' && t.length && t.length <= 8) : [];
  } catch {
    return [];
  }
};

const write = (arr) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(arr));
    listeners.forEach((fn) => {
      try { fn(arr); } catch (e) {
        // eslint-disable-next-line no-console
        console.debug('recentTickers listener threw:', e);
      }
    });
  } catch (e) {
    // Likely quota / private-browsing — non-critical.
    // eslint-disable-next-line no-console
    console.debug('recentTickers write failed:', e);
  }
};

export const getRecent = () => read();

export const addRecent = (ticker) => {
  const t = (ticker || '').toString().trim().toUpperCase();
  if (!t || t.length > 8) return;
  const cur = read().filter((x) => x !== t);
  cur.unshift(t);
  write(cur.slice(0, MAX));
};

export const subscribeRecent = (fn) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};
