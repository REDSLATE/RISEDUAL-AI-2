import { useCallback, useEffect, useState } from 'react';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * MC-aware context for the AI Assistant workspace.
 *
 * Polls three read-only surfaces:
 *   - `/api/admin/mc-sidecar/status`     → live sidecar + heartbeat health
 *   - `/api/sovereign/honesty-mirror`    → council disagreement / penalty stats
 *   - `/api/chat/mc/intents/recent`      → last N intents posted by Alpha
 *
 * Owner-only endpoints will return a 403 silently — the hook surfaces an
 * `ownerScope` flag so panels can render "operator-only" placeholders
 * instead of empty cards.
 *
 * Doctrine: this hook ONLY reads. Nothing here should mutate MC state.
 * Slash-command writes go through `/api/chat/mc/dispatch` in useChat.js.
 *
 * @param {{ intervalMs?: number }} [opts]
 */
export default function useMCContext({ intervalMs = 30_000 } = {}) {
  const [sidecar, setSidecar] = useState(null);
  const [mirror, setMirror] = useState(null);
  const [intents, setIntents] = useState(null);
  const [ownerScope, setOwnerScope] = useState(null); // null = unknown
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);

  const fetchAll = useCallback(async () => {
    let ownerSeen = false;
    try {
      const [scRes, hmRes, inRes] = await Promise.allSettled([
        authFetch(`${API}/admin/mc-sidecar/status`),
        authFetch(`${API}/sovereign/honesty-mirror`),
        authFetch(`${API}/chat/mc/intents/recent?limit=10`),
      ]);

      // Sidecar — owner-only. 403 → not an operator.
      if (scRes.status === 'fulfilled' && scRes.value.ok) {
        ownerSeen = true;
        setSidecar(await scRes.value.json());
      } else if (scRes.status === 'fulfilled' && scRes.value.status === 403) {
        setSidecar(null);
      }

      // Honesty mirror — owner-only.
      if (hmRes.status === 'fulfilled' && hmRes.value.ok) {
        ownerSeen = true;
        setMirror(await hmRes.value.json());
      } else if (hmRes.status === 'fulfilled' && hmRes.value.status === 403) {
        setMirror(null);
      }

      // Recent intents — owner-only.
      if (inRes.status === 'fulfilled' && inRes.value.ok) {
        ownerSeen = true;
        const payload = await inRes.value.json();
        if (!payload.error) setIntents(payload);
      } else if (inRes.status === 'fulfilled' && inRes.value.status === 403) {
        setIntents(null);
      }

      setOwnerScope(ownerSeen);
    } catch {
      // Network/transport — leave previous state alone, the next tick retries.
    } finally {
      setLoading(false);
      setLastUpdated(new Date());
    }
  }, []);

  useEffect(() => {
    fetchAll();
    if (!intervalMs) return undefined;
    const id = setInterval(fetchAll, intervalMs);
    return () => clearInterval(id);
  }, [fetchAll, intervalMs]);

  return {
    sidecar,
    mirror,
    intents,
    ownerScope,        // true if any owner endpoint succeeded; false if all 403
    loading,
    lastUpdated,
    refresh: fetchAll,
  };
}
