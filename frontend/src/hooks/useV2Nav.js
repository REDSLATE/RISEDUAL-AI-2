import { useEffect, useState } from 'react';

/**
 * useV2Nav — feature flag for the consolidated navigation (Phase 1).
 *
 * Enabled when:
 *   - URL has `?v2=1` (highest priority; also persists)
 *   - `localStorage.risedualai_v2_nav` === '1'
 *
 * Disable with `?v2=0` (clears localStorage).
 */
const STORAGE_KEY = 'risedualai_v2_nav';

function readFlag() {
  if (typeof window === 'undefined') return false;
  try {
    const url = new URL(window.location.href);
    const p = url.searchParams.get('v2');
    if (p === '1') {
      localStorage.setItem(STORAGE_KEY, '1');
      return true;
    }
    if (p === '0') {
      localStorage.removeItem(STORAGE_KEY);
      return false;
    }
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

export default function useV2Nav() {
  const [enabled, setEnabled] = useState(readFlag);

  useEffect(() => {
    const onStorage = () => setEnabled(readFlag());
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const toggle = (on) => {
    const next = on === undefined ? !enabled : !!on;
    if (next) localStorage.setItem(STORAGE_KEY, '1');
    else localStorage.removeItem(STORAGE_KEY);
    setEnabled(next);
  };

  return { enabled, toggle };
}
