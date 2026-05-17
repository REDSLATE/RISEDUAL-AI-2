"""Chat ↔ Mission Control bridge — slash command dispatcher.

This route is the **read-through surface** between the RISEDUAL chat UI
and Mission Control's live state. It is NOT a tool-using LLM agent —
that would blur authority lines. Instead, it dispatches a small set
of doctrinally-bounded slash commands and returns structured payloads
that the chat can render inline as MC cards.

Authority doctrine (Headless Brain V3):
    * The chat can ASK    (`/mc status`, `/mc mirror`, `/mc intents`)
    * The chat can DISPLAY (renders MC's reply as a card)
    * The chat can OPINE  (`/mc opine NVDA` → runs a fresh consensus,
                            returns the receipt; does NOT post-intent)
    * The chat CANNOT execute. Period. Intent emission stays inside
      the existing `intent_bridge` path used by the consensus tick.

Endpoints:
    POST /api/chat/mc/dispatch   → run a single slash command
    GET  /api/chat/mc/intents/recent  → list of recent shared_intents
                                         posted by this brain (Mongo
                                         doc, not an MC round-trip —
                                         the sidecar already mirrors
                                         emissions into a local
                                         collection for audit).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.auth_helpers import get_current_user, get_optional_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat/mc", tags=["chat", "mc"])

# Module-level DB binder — wired by route_registry.
_db: Any = None


def set_db(db) -> None:
    global _db
    _db = db


# ── helpers ────────────────────────────────────────────────────────────


_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,7}$")


def _normalize_symbol(raw: str) -> str | None:
    """Validate a ticker — uppercase, alphanumeric, max 8 chars."""
    if not raw:
        return None
    s = raw.strip().upper()
    return s if _SYMBOL_RE.match(s) else None


async def _is_owner(request: Request) -> bool:
    """Soft owner check — returns False instead of raising.

    Uses the canonical ``role == "owner"`` check (matches the rest of
    the codebase in ``routes/auth.py``). Note: a small number of older
    admin routes incorrectly check ``user.get("is_owner")`` which is
    never set on the user document — those are pre-existing bugs and
    will be unified in a follow-up sweep.
    """
    try:
        user = await get_current_user(request)
        return bool(user and user.get("role") == "owner")
    except HTTPException:
        return False


# ── dispatch model ─────────────────────────────────────────────────────


class SlashCommand(BaseModel):
    """A single slash command emitted by the chat parser."""
    command: str = Field(..., description="bare command: status|mirror|intents|opine|help")
    args: list[str] = Field(default_factory=list)


# ── command handlers ───────────────────────────────────────────────────


async def _cmd_status(request: Request) -> dict[str, Any]:
    """Mirror of /api/admin/mc-sidecar/status, owner-only."""
    if not await _is_owner(request):
        return {
            "kind": "mc_status",
            "error": "Owner-only command. Sign in as an operator to see sidecar state.",
        }
    try:
        from services import mc_sidecar
        if _db is None:
            return {"kind": "mc_status", "error": "DB not wired."}
        payload = await mc_sidecar.status(_db)
        # Strip noisy internals — chat card only needs the essentials.
        return {
            "kind": "mc_status",
            "running": payload.get("status") == "running",
            "heartbeat_alive": payload.get("heartbeat_task_alive", False),
            "contribution_alive": payload.get("contribution_task_alive", False),
            "watchdog_alive": payload.get("watchdog_task_alive", False),
            "last_heartbeat_at": payload.get("last_heartbeat_at"),
            "last_contribution_at": payload.get("last_contribution_at"),
            "last_error": payload.get("last_error"),
            "liveness_age_s": (payload.get("watchdog") or {}).get("liveness_age_s"),
            "identity": payload.get("identity"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("/mc status failed: %s", exc)
        return {"kind": "mc_status", "error": f"sidecar unreachable: {type(exc).__name__}"}


async def _cmd_mirror(request: Request, hours: int = 24) -> dict[str, Any]:
    """Owner-only proxy to MC's intent honesty aggregate."""
    if not await _is_owner(request):
        return {
            "kind": "mc_mirror",
            "error": "Owner-only command. Honesty mirror is internal-only.",
        }
    base = os.environ.get("MC_BASE_URL", "").rstrip("/")
    token = os.environ.get("ALPHA_INGEST_TOKEN", "")
    if not base:
        return {"kind": "mc_mirror", "error": "MC_BASE_URL not configured."}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=6.0, write=5.0, pool=2.0),
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            r = await client.get(
                f"{base}/api/admin/intents/honesty",
                params={"stack": "alpha", "hours": hours},
                headers={"X-Runtime-Token": token} if token else {},
            )
        if r.status_code != 200:
            return {
                "kind": "mc_mirror",
                "error": f"MC returned {r.status_code}",
                "detail": (r.text or "")[:160],
            }
        data = r.json() or {}
        return {
            "kind": "mc_mirror",
            "stack": "alpha",
            "hours": hours,
            "total_intents": data.get("total_intents", 0),
            "blocked_directional": data.get("blocked_directional", 0),
            "by_reason": data.get("by_reason", {}),
            "penalty_distribution": data.get("penalty_distribution", []),
        }
    except httpx.HTTPError as exc:
        logger.warning("/mc mirror MC fetch failed: %s", exc)
        return {"kind": "mc_mirror", "error": f"MC unreachable: {type(exc).__name__}"}


async def _cmd_intents(request: Request, limit: int = 10) -> dict[str, Any]:
    """List the most recent intents emitted by this brain.

    Reads from the local ``mc_intents_audit`` mirror collection that the
    intent bridge writes alongside the MC POST. This is intentionally
    a local read (no MC round-trip) so the chat stays responsive even
    when MC is offline.
    """
    if not await _is_owner(request):
        return {
            "kind": "mc_intents",
            "error": "Owner-only command. Recent intents are audit data.",
        }
    if _db is None:
        return {"kind": "mc_intents", "error": "DB not wired."}
    try:
        cursor = _db["mc_intents_audit"].find(
            {},
            {
                "_id": 0,
                "symbol": 1, "side": 1, "confidence": 1, "raw_action": 1,
                "market_decision": 1, "execution_decision": 1,
                "hold_reason": 1, "council_penalty": 1, "created_at": 1,
            },
        ).sort("created_at", -1).limit(max(1, min(limit, 50)))
        rows = []
        async for doc in cursor:
            rows.append(doc)
        emit_on = os.environ.get("RISEDUAL_EMIT_INTENTS_TO_MC", "0") == "1"
        return {
            "kind": "mc_intents",
            "count": len(rows),
            "intents": rows,
            "emission_enabled": emit_on,
            "note": None if rows else (
                "No audited intents yet."
                if emit_on else
                "Intent emission is off (RISEDUAL_EMIT_INTENTS_TO_MC=0)."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("/mc intents read failed: %s", exc)
        return {"kind": "mc_intents", "error": f"db read failed: {type(exc).__name__}"}


async def _cmd_opine(request: Request, symbol: str) -> dict[str, Any]:
    """Run a fresh consensus hypothesis. Read-only — no MC post."""
    sym = _normalize_symbol(symbol)
    if not sym:
        return {"kind": "mc_opine", "error": f"invalid symbol: {symbol!r}"}

    # Anyone signed in can call opine — it's the read-through into the
    # council. The hypothesis route already gates pro-vs-free for the
    # heavyweight models; we use the same path so doctrine stays
    # centralized.
    user = await get_optional_user(request)
    if not user:
        return {
            "kind": "mc_opine",
            "error": "Sign in to ask Alpha for a council opinion.",
        }

    try:
        from routes.market_data import _collect_all_scrape_data
        from services.multi_model_hypothesis_service import generate_hypothesis

        data = await _collect_all_scrape_data()
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        hyp = await generate_hypothesis(api_key, sym, data, model="alpha")

        return {
            "kind": "mc_opine",
            "symbol": sym,
            "verdict": hyp.get("verdict"),
            "confidence": hyp.get("confidence"),
            "market_decision": hyp.get("market_decision"),
            "execution_decision": hyp.get("execution_decision"),
            "display_action": hyp.get("display_action"),
            "hold_reason": hyp.get("hold_reason"),
            "raw_action": hyp.get("raw_action"),
            "council_penalty": hyp.get("council_penalty"),
            "would_have_traded_without_gates": hyp.get(
                "would_have_traded_without_gates",
            ),
            "summary": (hyp.get("summary") or hyp.get("rationale") or "")[:600],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("/mc opine %s failed: %s", sym, exc)
        return {"kind": "mc_opine", "symbol": sym, "error": f"council fetch failed: {type(exc).__name__}"}


_HELP_CARD = {
    "kind": "mc_help",
    "commands": [
        {"cmd": "/mc status", "desc": "Live sidecar + heartbeat health (owner)"},
        {"cmd": "/mc mirror", "desc": "Honesty mirror — intent audit (owner)"},
        {"cmd": "/mc intents", "desc": "Last 10 intents posted to MC (owner)"},
        {"cmd": "/mc opine <ticker>", "desc": "Run a fresh council hypothesis"},
        {"cmd": "/mc help", "desc": "Show this card"},
    ],
}


# ── route handler ──────────────────────────────────────────────────────


@router.post("/dispatch")
async def dispatch(cmd: SlashCommand, request: Request) -> dict[str, Any]:
    """Single entry point for all `/mc <verb>` slash commands.

    Returns a structured card payload; the chat renders the card based
    on the ``kind`` discriminator (``mc_status`` / ``mc_mirror`` /
    ``mc_intents`` / ``mc_opine`` / ``mc_help``).
    """
    verb = (cmd.command or "").strip().lower()
    args = [a for a in (cmd.args or []) if a]
    if verb in ("", "help"):
        return _HELP_CARD
    if verb == "status":
        return await _cmd_status(request)
    if verb == "mirror":
        hours = 24
        if args:
            try:
                hours = max(1, min(168, int(args[0])))
            except ValueError:
                pass
        return await _cmd_mirror(request, hours=hours)
    if verb == "intents":
        limit = 10
        if args:
            try:
                limit = max(1, min(50, int(args[0])))
            except ValueError:
                pass
        return await _cmd_intents(request, limit=limit)
    if verb == "opine":
        if not args:
            return {"kind": "mc_opine", "error": "Usage: /mc opine <ticker>"}
        return await _cmd_opine(request, symbol=args[0])
    return {"kind": "mc_help", "error": f"Unknown command: /mc {verb}", **_HELP_CARD}


@router.get("/intents/recent")
async def recent_intents(request: Request, limit: int = 10) -> dict[str, Any]:
    """Convenience GET wrapper around the intents command — used by
    the right-pane "AI Recommendations" panel on first render so the
    chat doesn't have to dispatch a slash to populate it."""
    return await _cmd_intents(request, limit=limit)


__all__ = ["router", "set_db"]
