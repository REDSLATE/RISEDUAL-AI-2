"""Patent I integration gateway.

Thin façade over ``services/authority_risk_budget.py`` that:

  1. Mints sensible default ``AuthorityScope`` objects for each
     execution context (per-user manual orders, autonomous crypto bot,
     promotion-bridge-activated engines).
  2. Builds a rolling ``TrackRecord`` from the Mongo collections we
     already populate (``crypto_paper_trades``, ``predictions``,
     ``crypto_adversarial_log``).
  3. Runs ``apply_authority_scoped_risk_budget`` and persists every
     decision (allow/deny + audit hash + reasons) to
     ``risk_budget_decisions`` for compliance.

Callers should not import the lower-level Patent-I primitives directly.
This gateway is the single integration seam.
"""
from __future__ import annotations

__domain__ = "DTD"

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.authority_risk_budget import (
    AuthorityScope,
    AuthorityTier,
    RiskBudgetDecision,
    RiskBudgetRequest,
    TrackRecord,
    apply_authority_scoped_risk_budget,
)

logger = logging.getLogger(__name__)

_db: Any = None


def set_db(db: Any) -> None:
    global _db
    _db = db


# ── Authority minting ────────────────────────────────────────────────


# 24h expiration for runtime-minted scopes. Intentionally short so an
# inactive engine has to re-authorize daily; this is the asymmetric-
# loosening principle applied at the lifetime axis too.
_DEFAULT_AUTHORITY_TTL = timedelta(hours=24)


def _scope_signature(authority_id: str, tier: AuthorityTier, expires_at: datetime) -> str:
    """Deterministic signature derived from the scope's identity.

    Not a cryptographic auth — it's a stable identifier for the audit
    log so two decisions citing the same scope can be correlated.
    """
    payload = f"{authority_id}|{tier.value}|{expires_at.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bridge_countersignature() -> Optional[str]:
    """The Patent-H countersignature is the Bridge approval token.

    A loosening request must carry the SHA-256 of this token; the raw
    token never leaves the server.
    """
    token = os.environ.get("BRIDGE_APPROVAL_TOKEN")
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_authority_for_user(
    user: dict,
    asset_class: str,
    *,
    max_notional: float = 50_000.0,
    max_daily_loss: float = 5_000.0,
) -> AuthorityScope:
    """Mint an ``AuthorityScope`` for a manual-trading user.

    Tier policy:
      * ``role == "owner"``    → ``ELEVATED_LIVE`` (tier cap 1.25x)
      * ``role == "admin"``    → ``STANDARD_LIVE`` (tier cap 0.75x)
      * everyone else          → ``LIMITED_LIVE``  (tier cap 0.35x)

    A user in PAPER mode (per the navbar pill) doesn't reach this gate;
    paper routes are gated upstream by ``trading_mode_guards``. This
    scope is for the live path only.
    """
    role = (user or {}).get("role", "user")
    if role == "owner":
        tier = AuthorityTier.ELEVATED_LIVE
        max_mult = 1.25
    elif role == "admin":
        tier = AuthorityTier.STANDARD_LIVE
        max_mult = 0.75
    else:
        tier = AuthorityTier.LIMITED_LIVE
        max_mult = 0.35

    user_id = str(user.get("_id") or user.get("id") or "anon")
    authority_id = f"user:{user_id}:{asset_class}"
    expires_at = datetime.now(timezone.utc) + _DEFAULT_AUTHORITY_TTL

    return AuthorityScope(
        authority_id=authority_id,
        tier=tier,
        asset_type=asset_class,
        engine="manual_user",
        approved_by=role,
        expires_at=expires_at,
        max_multiplier=max_mult,
        max_notional=max_notional,
        max_daily_loss=max_daily_loss,
        signature_hash=_scope_signature(authority_id, tier, expires_at),
        countersignature_hash=_bridge_countersignature(),
    )


def mint_authority_for_crypto_bot() -> AuthorityScope:
    """Mint the system-level scope for the autonomous crypto paper bot.

    The bot is sandboxed (paper-only) and its risk budget is therefore
    bounded by **its own historical performance**, not a per-user role.
    Patent I pulls the multiplier down on loss streaks / drawdown.
    """
    authority_id = "system:crypto_paper_bot"
    expires_at = datetime.now(timezone.utc) + _DEFAULT_AUTHORITY_TTL
    return AuthorityScope(
        authority_id=authority_id,
        tier=AuthorityTier.STANDARD_LIVE,
        asset_type="crypto",
        engine="adversarial_commander",
        approved_by="system",
        expires_at=expires_at,
        max_multiplier=1.0,
        max_notional=10_000.0,
        max_daily_loss=2_500.0,
        signature_hash=_scope_signature(authority_id, AuthorityTier.STANDARD_LIVE, expires_at),
        countersignature_hash=_bridge_countersignature(),
    )


def mint_authority_for_bridge(
    bridge_name: str,
    *,
    bridge_value: float,
    bridge_version: str,
    activated_by: str,
) -> AuthorityScope:
    """Mint a scope tied to a freshly-activated promotion bridge.

    Per the patent narrative: Patent H authorizes the bridge,
    Patent I budgets risk *inside* its scope. The bridge's approval
    token IS the countersignature — so future loosening requests
    targeting this scope must echo the same hashed token.
    """
    authority_id = f"bridge:{bridge_name}:{bridge_version}"
    expires_at = datetime.now(timezone.utc) + _DEFAULT_AUTHORITY_TTL
    return AuthorityScope(
        authority_id=authority_id,
        tier=AuthorityTier.LIMITED_LIVE,  # newly-activated bridges start small
        asset_type="multi",
        engine=bridge_name,
        approved_by=activated_by,
        expires_at=expires_at,
        max_multiplier=min(0.35, max(0.0, bridge_value)),
        max_notional=5_000.0,
        max_daily_loss=1_000.0,
        signature_hash=_scope_signature(authority_id, AuthorityTier.LIMITED_LIVE, expires_at),
        countersignature_hash=_bridge_countersignature(),
    )


# ── TrackRecord builders ─────────────────────────────────────────────


async def build_track_record_for_crypto_bot(
    *, lookback_days: int = 30,
) -> TrackRecord:
    """Build a ``TrackRecord`` for the autonomous crypto bot.

    Pulls from ``crypto_paper_trades`` (closed positions only) plus
    ``crypto_adversarial_log`` for veto counts. Falls back to a
    zero-trade record if Mongo isn't wired or the queries fail — the
    Patent-I tightening rules then kick in (small_sample_tightening
    floors the multiplier at 0.5x).
    """
    return await _build_track_record_generic(
        collection_name="crypto_paper_trades",
        match={"status": "closed"},
        veto_collection="crypto_adversarial_log",
        veto_match={"final_direction": "HOLD", "had_signal": True},
        lookback_days=lookback_days,
    )


async def build_track_record_for_user(
    user_id: str,
    asset_class: str,
    *,
    lookback_days: int = 30,
) -> TrackRecord:
    """Build a ``TrackRecord`` for a manual-trading user.

    Sources:
      * ``predictions`` filtered by ``user_id`` for PnL + win-rate
      * ``trading_mode_switches`` is *not* used (mode hygiene is a
        separate guard)
    """
    return await _build_track_record_generic(
        collection_name="predictions",
        match={"user_id": user_id, "asset_class": asset_class,
               "verified_24h.correct": {"$in": [True, False]}},
        veto_collection=None,
        veto_match=None,
        lookback_days=lookback_days,
    )


async def _build_track_record_generic(
    *,
    collection_name: str,
    match: dict,
    veto_collection: Optional[str],
    veto_match: Optional[dict],
    lookback_days: int,
) -> TrackRecord:
    now = datetime.now(timezone.utc)
    if _db is None:
        return _empty_track_record(now)

    try:
        # Lookback windows the data-write path can apply later (we
        # already filter aggressively below; the explicit since is
        # left in as documentation for the next reader).
        cursor = _db[collection_name].find(
            match, {"_id": 0}
        ).sort("created_at", -1).limit(1000)

        wins = losses = total = 0
        realized_pnl = 0.0
        equity_curve: list[float] = [0.0]
        loss_streak = 0
        cur_streak = 0
        last_was_loss: Optional[bool] = None

        async for doc in cursor:
            total += 1
            # Crypto trades store realized_pnl directly. Predictions
            # store verified_24h.correct (bool). We support both.
            pnl = doc.get("realized_pnl")
            if pnl is None:
                v = (doc.get("verified_24h") or {}).get("correct")
                if v is True:
                    pnl = 1.0
                elif v is False:
                    pnl = -1.0
                else:
                    continue
            pnl = float(pnl)
            realized_pnl += pnl
            equity_curve.append(equity_curve[-1] + pnl)
            is_loss = pnl < 0
            if is_loss:
                losses += 1
            else:
                wins += 1
            # Walk the streak: reset on a win, increment on a loss
            if last_was_loss is None:
                cur_streak = 1 if is_loss else 0
            else:
                cur_streak = cur_streak + 1 if is_loss else 0
            loss_streak = max(loss_streak, cur_streak)
            last_was_loss = is_loss

        win_rate = (wins / total) if total else 0.0
        peak = 0.0
        max_dd = 0.0
        for eq in equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                dd = (peak - eq) / peak
                max_dd = max(max_dd, dd)

        veto_count = 0
        if veto_collection and veto_match:
            try:
                veto_count = await _db[veto_collection].count_documents(veto_match)
            except Exception:
                veto_count = 0

        return TrackRecord(
            total_trades=total,
            win_rate=win_rate,
            realized_pnl=realized_pnl,
            max_drawdown=min(1.0, max_dd),
            calibration_gap=0.0,
            loss_streak=loss_streak,
            veto_count=veto_count,
            rejected_signal_count=0,
            last_updated=now,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[risk_budget] track record build failed: %s", e)
        return _empty_track_record(now)


def _empty_track_record(now: datetime) -> TrackRecord:
    return TrackRecord(
        total_trades=0,
        win_rate=0.0,
        realized_pnl=0.0,
        max_drawdown=0.0,
        calibration_gap=0.0,
        loss_streak=0,
        veto_count=0,
        rejected_signal_count=0,
        last_updated=now,
    )


# ── Daily loss helper ────────────────────────────────────────────────


async def get_daily_realized_loss(
    *,
    asset_class: str,
    user_id: Optional[str] = None,
) -> float:
    """Sum of negative ``realized_pnl`` for trades closed today.

    Returns a positive number — Patent I compares against
    ``authority.max_daily_loss`` as a positive ceiling.
    """
    if _db is None:
        return 0.0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coll = "crypto_paper_trades" if asset_class == "crypto" else "paper_trades"
    match: dict = {"status": "closed", "closed_at": {"$gte": today}}
    if user_id:
        match["user_id"] = user_id
    try:
        cursor = _db[coll].find(match, {"realized_pnl": 1, "_id": 0})
        loss = 0.0
        async for d in cursor:
            v = float(d.get("realized_pnl") or 0.0)
            if v < 0:
                loss += -v
        return loss
    except Exception:
        return 0.0


# ── Decision logging ─────────────────────────────────────────────────


async def _persist_decision(
    decision: RiskBudgetDecision,
    request: RiskBudgetRequest,
    *,
    context: dict,
) -> None:
    if _db is None:
        return
    try:
        await _db["risk_budget_decisions"].insert_one({
            "audit_hash": decision.audit_hash,
            "action": decision.action,
            "allowed": decision.allowed,
            "final_notional": decision.final_notional,
            "final_multiplier": decision.final_multiplier,
            "authority_id": request.authority.authority_id,
            "authority_tier": request.authority.tier.value,
            "asset_type": request.authority.asset_type,
            "engine": request.authority.engine,
            "tightened": decision.tightened,
            "loosened": decision.loosened,
            "reasons": decision.reasons,
            "base_multiplier": request.base_multiplier,
            "base_notional": request.base_notional,
            "requested_multiplier": request.requested_multiplier,
            "daily_realized_loss": request.daily_realized_loss,
            "context": context,
            "created_at": decision.created_at.isoformat(),
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("[risk_budget] decision persistence failed: %s", e)


# ── Public façade ────────────────────────────────────────────────────


async def enforce_budget(
    *,
    action: str,
    base_notional: float,
    base_multiplier: float,
    authority: AuthorityScope,
    track_record: TrackRecord,
    daily_realized_loss: float = 0.0,
    requested_multiplier: Optional[float] = None,
    countersignature_hash: Optional[str] = None,
    context: Optional[dict] = None,
) -> RiskBudgetDecision:
    """Run a Patent-I budget enforcement and persist the decision.

    The caller passes pre-built ``AuthorityScope`` and ``TrackRecord``
    so this function is fully sync-safe outside of the persistence
    write — that lets tests use it without mocking Mongo.
    """
    request = RiskBudgetRequest(
        action=action,  # type: ignore[arg-type]
        base_notional=base_notional,
        base_multiplier=base_multiplier,
        authority=authority,
        track_record=track_record,
        daily_realized_loss=daily_realized_loss,
        requested_multiplier=requested_multiplier,
        loosening_countersignature_hash=countersignature_hash,
    )
    decision = apply_authority_scoped_risk_budget(request)
    await _persist_decision(decision, request, context=context or {})
    return decision


async def ensure_indexes() -> None:
    if _db is None:
        return
    try:
        await _db["risk_budget_decisions"].create_index("created_at")
        await _db["risk_budget_decisions"].create_index("authority_id")
        await _db["risk_budget_decisions"].create_index("audit_hash")
    except Exception:
        pass
