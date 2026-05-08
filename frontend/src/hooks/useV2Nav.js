import { useEffect, useState } from 'react';

/**
 * useV2Nav — v2 is the DEFAULT experience (shipped Feb 18, 2026).
 *
 * v1 (legacy) stays accessible as historical site data via:
 *   - URL param ?v1=1 (persists)
 *   - URL param ?v2=0 (legacy alias — also switches to v1)
 *   - localStorage.risedualai_legacy_nav === '1'
 *
 * Restore v2 with ?v1=0 or ?v2=1 (clears localStorage).
 */
const LEGACY_KEY = 'risedualai_legacy_nav';
const LEGACY_KEY_OLD = 'risedualai_v2_nav'; // old opt-in key — ignored going forward

function readFlag() {
  if (typeof window === 'undefined') return true;
  try {
    const url = new URL(window.location.href);
    const v1 = url.searchParams.get('v1');
    const v2 = url.searchParams.get('v2');

    // Explicit opt-in to legacy
    if (v1 === '1' || v2 === '0') {
      localStorage.setItem(LEGACY_KEY, '1');
      localStorage.removeItem(LEGACY_KEY_OLD);
      return false; // v2 disabled → show legacy
    }
    // Explicit opt-out of legacy
    if (v1 === '0' || v2 === '1') {
      localStorage.removeItem(LEGACY_KEY);
      localStorage.removeItem(LEGACY_KEY_OLD);
      return true;
    }
    // Persisted legacy preference
    if (localStorage.getItem(LEGACY_KEY) === '1') return false;
    return true;
  } catch {
    return true;
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
    if (next) localStorage.removeItem(LEGACY_KEY);
    else localStorage.setItem(LEGACY_KEY, '1');
    setEnabled(next);
  };

  return { enabled, toggle };
}
