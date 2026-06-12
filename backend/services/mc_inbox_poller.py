"""MC inbox poller — pulls opinions / roles / scorecard from Mission
Control and feeds them through Shelly's perception layer.

Doctrine:
    - We are a HEADLESS BRAIN under V3. Mission Control owns market
      data, news, sentiment, and broker keys. Brains receive intel
      through the cross-brain discussion layer.
    - This poller is the inbound half of the sidecar:
        OUT (sovereign/sidecar.py)  — contributions + heartbeats
        IN  (this file)             — opinions + roles + scorecard
    - Every inbound opinion is stamped through ``shelly.perceive()``
      so Doctrine v2 quarantine + provenance apply uniformly.
    - We never drop opinions at write time. Per-peer balancing is a
      READ-side concern (see ``balanced_recent_opinions``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mc_inbox_poller")


# ── Paths + tunables ───────────────────────────────────────────────


def _state_path() -> Path:
    return Path(os.environ.get(
        "MC_INBOX_STATE_PATH", "/app/data/mc_inbox/state.json",
    ))


# Polling cadences (seconds).
OPINIONS_POLL_SECONDS = int(os.environ.get("MC_INBOX_OPINIONS_INTERVAL", "60"))
ROLES_POLL_SECONDS = int(os.environ.get("MC_INBOX_ROLES_INTERVAL", "300"))
SCORECARD_POLL_SECONDS = int(os.environ.get("MC_INBOX_SCORECARD_INTERVAL", "600"))

# Soft per-poll cap; we use ``since`` cursors so this only matters if
# we restart after a long downtime.
OPINIONS_PAGE_LIMIT = int(os.environ.get("MC_INBOX_OPINIONS_LIMIT", "200"))


# ── State ──────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict[str, Any]:
    """Read the JSON state file. Missing/corrupt → fresh state."""
    p = _state_path()
    if not p.exists():
        return {"last_seen_opinion_at": None, "last_seen_opinion_id": None,
                "last_roles_at": None, "last_scorecard_at": None,
                "stats": {"opinions_ingested": 0, "opinions_quarantined": 0,
                          "by_peer": {}}}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[mc_inbox] state load failed (%s); starting fresh", e)
        return {"last_seen_opinion_at": None, "last_seen_opinion_id": None,
                "last_roles_at": None, "last_scorecard_at": None,
                "stats": {"opinions_ingested": 0, "opinions_quarantined": 0,
                          "by_peer": {}}}


def save_state(state: dict[str, Any]) -> None:
    """Atomic write — temp file + rename."""
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp",
                                dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True, default=str)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Selectors ──────────────────────────────────────────────────────


def filter_new_opinions(
    items: list[dict[str, Any]],
    *,
    last_seen_at: str | None,
    last_seen_id: str | None,
) -> list[dict[str, Any]]:
    """Return only opinions newer than the (last_seen_at, last_seen_id)
    cursor. Items without a ``created_at`` are passed through.

    Tie-breaker: if ``created_at`` equals ``last_seen_at`` exactly,
    we compare ``id``/``opinion_id`` to last_seen_id and skip
    everything lexicographically ≤ the cursor.
    """
    if not items:
        return []
    if last_seen_at is None:
        return list(items)
    fresh: list[dict[str, Any]] = []
    for it in items:
        ts = str(it.get("created_at") or it.get("at") or "")
        if not ts:
            fresh.append(it)
            continue
        if ts > last_seen_at:
            fresh.append(it)
        elif ts == last_seen_at:
            oid = str(it.get("id") or it.get("opinion_id") or "")
            if last_seen_id and oid and oid > last_seen_id:
                fresh.append(it)
    return fresh


def advance_cursor(
    state: dict[str, Any], new_items: list[dict[str, Any]],
) -> None:
    """Move the cursor forward to the latest item in ``new_items``.

    Assumes ``new_items`` are pre-filtered with ``filter_new_opinions``.
    """
    if not new_items:
        return
    latest = max(
        new_items,
        key=lambda i: (str(i.get("created_at") or i.get("at") or ""),
                       str(i.get("id") or i.get("opinion_id") or "")),
    )
    state["last_seen_opinion_at"] = str(latest.get("created_at") or latest.get("at") or "")
    state["last_seen_opinion_id"] = str(latest.get("id") or latest.get("opinion_id") or "")


# ── Per-peer balancing (read-side helper) ──────────────────────────


def balanced_recent_opinions(
    items: list[dict[str, Any]],
    *,
    max_per_peer: int = 5,
    total_cap: int = 20,
) -> list[dict[str, Any]]:
    """Given a list of opinions (newest-first), return a peer-balanced
    slice so a single chatty peer can't dominate.

    Round-robins through ``runtime`` values picking up to
    ``max_per_peer`` per peer, capped by ``total_cap`` overall.
    Preserves newest-first ordering within each peer.
    """
    if not items:
        return []
    # Group by peer in newest-first order (the input is already sorted
    # newest-first by the caller; we don't re-sort to avoid relying on
    # caller-supplied keys).
    by_peer: dict[str, list[dict[str, Any]]] = {}
    peer_order: list[str] = []
    for it in items:
        peer = str(it.get("runtime") or it.get("peer") or "unknown")
        if peer not in by_peer:
            peer_order.append(peer)
            by_peer[peer] = []
        if len(by_peer[peer]) < max_per_peer:
            by_peer[peer].append(it)

    # Round-robin across peers.
    out: list[dict[str, Any]] = []
    idx_per_peer: dict[str, int] = {p: 0 for p in peer_order}
    while len(out) < total_cap:
        progressed = False
        for peer in peer_order:
            i = idx_per_peer[peer]
            if i < len(by_peer[peer]):
                out.append(by_peer[peer][i])
                idx_per_peer[peer] = i + 1
                progressed = True
                if len(out) >= total_cap:
                    break
        if not progressed:
            break
    return out


# ── Ingest path ────────────────────────────────────────────────────


async def ingest_opinions_once(db, *, client=None, perceive_fn=None) -> dict[str, Any]:
    """One poll cycle. Returns a stats dict — never raises.

    ``client`` defaults to the module-level monorepo client.
    ``perceive_fn`` defaults to ``services.shelly_memory.perceive`` — the
    same callable, but reached via the re-export avoids a circular
    import in test environments. Both kwargs let tests inject stubs
    without touching the network or Shelly's storage.
    """
    if client is None:
        from services import risedual_monorepo_client as client  # type: ignore[no-redef]
    if perceive_fn is None:
        # Re-export from shelly_memory side-steps the circular-import
        # path that bites pytest's monkeypatch resolver.
        from services.shelly_memory import perceive as perceive_fn  # type: ignore[no-redef]

    state = load_state()
    last_at = state.get("last_seen_opinion_at")
    last_id = state.get("last_seen_opinion_id")

    try:
        resp = await client.read_opinions(
            limit=OPINIONS_PAGE_LIMIT,
            since=last_at,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[mc_inbox] read_opinions failed: %s", e)
        return {"polled": 0, "fresh": 0, "ingested": 0, "quarantined": 0,
                "error": str(e)}

    if resp.get("error"):
        logger.debug("[mc_inbox] opinions endpoint sentinel: %s", resp["error"])
        return {"polled": 0, "fresh": 0, "ingested": 0, "quarantined": 0,
                "error": resp["error"]}

    items = list(resp.get("items") or [])
    fresh = filter_new_opinions(items, last_seen_at=last_at, last_seen_id=last_id)

    ingested = 0
    quarantined = 0
    by_peer: dict[str, int] = dict(state.get("stats", {}).get("by_peer", {}))

    for op in fresh:
        peer = str(op.get("runtime") or op.get("peer") or "unknown")
        try:
            result = await perceive_fn(
                db,
                payload=op,
                source=f"mc.opinion.{peer}",
                text=_opinion_text(op),
                metadata={
                    "peer": peer,
                    "topic": op.get("topic"),
                    "stance": op.get("stance"),
                    "confidence": op.get("confidence"),
                    "opinion_id": op.get("id") or op.get("opinion_id"),
                    "thread_root": op.get("thread_root"),
                    "in_reply_to": op.get("in_reply_to"),
                    "evidence_keys": (
                        list(op["evidence"].keys())
                        if isinstance(op.get("evidence"), dict) else None
                    ),
                },
            )
            if result.get("lane") == "malformed":
                quarantined += 1
            else:
                ingested += 1
            by_peer[peer] = by_peer.get(peer, 0) + 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[mc_inbox] perceive failed for opinion from %s: %s", peer, e,
            )

    advance_cursor(state, fresh)
    stats = state.setdefault("stats", {"opinions_ingested": 0,
                                       "opinions_quarantined": 0,
                                       "by_peer": {}})
    stats["opinions_ingested"] = int(stats.get("opinions_ingested", 0)) + ingested
    stats["opinions_quarantined"] = int(stats.get("opinions_quarantined", 0)) + quarantined
    stats["by_peer"] = by_peer
    state["last_polled_at"] = _now_iso()

    try:
        save_state(state)
    except Exception as e:  # noqa: BLE001
        logger.warning("[mc_inbox] save_state failed: %s", e)

    return {"polled": len(items), "fresh": len(fresh),
            "ingested": ingested, "quarantined": quarantined,
            "by_peer_delta": {
                p: by_peer.get(p, 0) for p in {
                    str(o.get("runtime") or "unknown") for o in fresh
                }
            }}


def _opinion_text(op: dict[str, Any]) -> str:
    """Coerce an opinion payload to the canonical short text Shelly
    likes to index. We use ``body`` if present, falling back to the
    stance/topic combination."""
    body = op.get("body")
    if isinstance(body, str) and body.strip():
        return body.strip()
    parts = [
        f"[{op.get('runtime', 'unknown')}]",
        op.get("stance", ""),
        f"on {op.get('topic', '')}" if op.get("topic") else "",
    ]
    return " ".join(p for p in parts if p)


async def refresh_roles_once(*, client=None) -> dict[str, Any]:
    """Pull the live roster + persist a snapshot of who holds what seat.
    Returns the manifest dict; ``error`` key set on failure.
    """
    if client is None:
        from services import risedual_monorepo_client as client  # type: ignore[no-redef]
    try:
        manifest = await client.read_roles_manifest()
    except Exception as e:  # noqa: BLE001
        logger.warning("[mc_inbox] read_roles_manifest failed: %s", e)
        return {"error": str(e), "items": []}

    state = load_state()
    state["last_roles_at"] = _now_iso()
    state["roles_snapshot"] = manifest
    try:
        save_state(state)
    except Exception as e:  # noqa: BLE001
        logger.warning("[mc_inbox] save_state failed (roles): %s", e)
    return manifest


async def refresh_scorecard_once(*, client=None) -> dict[str, Any]:
    """Pull this brain's scorecard. Stores in state for downstream UIs."""
    if client is None:
        from services import risedual_monorepo_client as client  # type: ignore[no-redef]
    try:
        sc = await client.read_my_scorecard()
    except Exception as e:  # noqa: BLE001
        logger.warning("[mc_inbox] read_my_scorecard failed: %s", e)
        return {"error": str(e), "summary": {}}

    state = load_state()
    state["last_scorecard_at"] = _now_iso()
    state["scorecard_snapshot"] = sc
    try:
        save_state(state)
    except Exception as e:  # noqa: BLE001
        logger.warning("[mc_inbox] save_state failed (scorecard): %s", e)
    return sc


# ── Background loops (used by the supervisor process) ─────────────


async def _opinions_loop(db) -> None:
    while True:
        try:
            stats = await ingest_opinions_once(db)
            if stats.get("fresh"):
                logger.info(
                    "[mc_inbox] opinions tick: fresh=%d ingested=%d "
                    "quarantined=%d", stats["fresh"], stats["ingested"],
                    stats["quarantined"],
                )
        except Exception as e:  # noqa: BLE001
            logger.exception("[mc_inbox] opinions_loop crashed: %s", e)
        await asyncio.sleep(OPINIONS_POLL_SECONDS)


async def _roles_loop() -> None:
    while True:
        try:
            await refresh_roles_once()
            logger.debug("[mc_inbox] roles refreshed")
        except Exception as e:  # noqa: BLE001
            logger.exception("[mc_inbox] roles_loop crashed: %s", e)
        await asyncio.sleep(ROLES_POLL_SECONDS)


async def _scorecard_loop() -> None:
    while True:
        try:
            await refresh_scorecard_once()
            logger.debug("[mc_inbox] scorecard refreshed")
        except Exception as e:  # noqa: BLE001
            logger.exception("[mc_inbox] scorecard_loop crashed: %s", e)
        await asyncio.sleep(SCORECARD_POLL_SECONDS)


async def run_forever(db) -> None:
    """Top-level entry — run all three loops concurrently.

    2026-06-09 MC2 severance: when ``RISEDUAL_STANDALONE_MODE=1``,
    all three loops (opinions / roles / scorecard) are skipped.
    The whole point of MC inbox polling was to pull peer state from
    Original MC — in standalone mode there's nothing remote to pull
    from. Returns immediately so the scheduler stops trying to wake
    these tasks.
    """
    try:
        from services.mc2 import is_standalone
        if is_standalone():
            logger.info(
                "[mc_inbox] RISEDUAL_STANDALONE_MODE=1 — Original MC inbox "
                "polling SKIPPED (opinions / roles / scorecard loops dead)"
            )
            return
    except Exception:  # noqa: BLE001
        # If the MC2 module isn't importable for any reason, fall
        # through to legacy behaviour — better to keep polling than
        # silently break the inbox refresh.
        pass

    logger.info(
        "[mc_inbox] starting: opinions=%ds roles=%ds scorecard=%ds state=%s",
        OPINIONS_POLL_SECONDS, ROLES_POLL_SECONDS, SCORECARD_POLL_SECONDS,
        _state_path(),
    )
    await asyncio.gather(
        _opinions_loop(db),
        _roles_loop(),
        _scorecard_loop(),
    )
