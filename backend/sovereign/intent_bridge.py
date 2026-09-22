"""Fire a consensus receipt to MC as an intent.

This is the one-call wiring point — given a doctrine receipt from
``_weighted_consensus``, emit a properly-shaped intent POST to MC's
``/api/intents`` with the full honesty trailer attached.

Doctrine guard rails baked in:

  * Only directional verdicts (BUY / SELL / SHORT / COVER) trigger an
    emission. ``ALL_HOLD`` consensus passes through silently because
    "I have no opinion" is not an intent — emitting it would pollute
    MC's intent feed with no-ops.
  * RISEDUAL is doctrinally headless under V3 — every intent is
    stamped ``execution_decision="OBSERVE_ONLY"`` so MC's executor
    seat can route at will without thinking we're claiming execution
    authority.
  * Quantity is unit-scale (``1.0``) by default — Alpha is an
    advisor/decider, not a sizer. MC's executor multiplies by its
    own sizing model. Callers that DO know a desired size can pass
    ``qty=`` explicitly.
  * Fire-and-forget: emission is a sync call wrapped in
    ``run_in_executor`` from async callers, and exceptions are
    swallowed with a log line. A flaky MC must never block a brain
    from producing the next consensus.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Mapping

from shared.runtime.platform_survival import (
    mc_canonical_gate as _survival_gate,
    sidecar_build_intent as _survival_build_intent,
)
from sovereign.intent_receipt import consensus_receipt_to_intent_fields
from sovereign.mc_client import MCClient, MCClientError

logger = logging.getLogger(__name__)


# Verdicts the bridge will emit. HOLD / NEUTRAL / unknown verdicts
# are not intents — they're opinions and stay in the local receipt
# only. (`ALLOWED_ACTIONS` over in mc_client.py also includes HOLD
# for the schema's sake; this set is stricter on purpose.)
_DIRECTIONAL = frozenset({"BUY", "SELL", "SHORT", "COVER"})

# Survival layer wiring (2026-05-17 Phase A2):
#
# Every emission now runs through ``mc_canonical_gate`` locally as a
# pre-flight mirror of what the remote MC will decide. The kernel
# attaches a stamped runtime envelope (env / git_sha / policy_hash /
# local_execution_authority=False) and either approves with an
# HMAC-signed receipt or surfaces an explicit reason string.
#
# Mode is governed by ``RISEDUAL_SURVIVAL_ENFORCE``:
#   * unset/0  → soft-warn (current default). Pre-flight failures log
#                a `SURVIVAL_PREFLIGHT_DENY` line but emission still
#                fires. Lets us observe the kernel in prod without
#                gating real flow.
#   * 1        → hard-block. Failed pre-flight short-circuits the
#                emission and returns None — same effect as a non-
#                directional verdict.
#
# Broker-side verification lives in the lane executors; this is the
# brain-side half of the wiring.
_SURVIVAL_ENFORCE = os.environ.get("RISEDUAL_SURVIVAL_ENFORCE", "0") == "1"


# Crypto-vs-equity lane classifier. Used only to tag the trace ID so
# the operator can grep CRYPTO vs EQUITY across the pipeline without
# touching anything else in the council math.
_CRYPTO_HINTS = ("/USD", "/USDT", "/USDC", "-USD", "BTC", "ETH", "SOL", "XRP",
                 "DOGE", "ADA", "BNB", "AVAX", "LINK", "MATIC", "DOT")


def _classify_lane(symbol: str) -> str:
    s = (symbol or "").upper()
    if any(h in s for h in _CRYPTO_HINTS):
        return "CRYPTO"
    return "EQUITY"


def _new_trace_id() -> str:
    """8-char trace id — short enough to grep, long enough to be unique."""
    return uuid.uuid4().hex[:8]


def _build_emission_kwargs(
    receipt: Mapping[str, Any], *, qty: float, notes: str,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Compose post_intent kwargs from a doctrine receipt.

    Returns None when the receipt is non-directional (caller should
    skip emission). Returns a kwargs dict ready to splat into
    ``MCClient.post_intent`` otherwise.

    A ``trace_id`` is auto-generated if not supplied and stamped onto
    the payload so the operator can follow one intent through
    Alpha → MC → executor → broker logs end-to-end.
    """
    raw = str(receipt.get("raw_action") or receipt.get("market_decision") or "").upper()
    if raw not in _DIRECTIONAL:
        return None

    # Final confidence sits on the receipt as percent. Clamp + scale.
    final_pct = receipt.get("final_confidence", receipt.get("confidence", 0))
    try:
        final_unit = max(0.0, min(1.0, float(final_pct) / 100.0))
    except (TypeError, ValueError):
        final_unit = 0.5

    symbol = str(receipt.get("symbol") or "").upper()
    if not symbol:
        return None

    # 2026-05-17 Operator Override: under Doctrine V3 the brain
    # *requests* execution from MC instead of stamping OBSERVE_ONLY.
    # MC's executor seat still owns the final yes/no; the brain
    # simply stops pre-filing every intent as advisory-only.
    receipt_for_bridge = dict(receipt)
    receipt_for_bridge.setdefault("execution_decision", "ALLOW")

    honesty = consensus_receipt_to_intent_fields(receipt_for_bridge)
    tid = trace_id or _new_trace_id()
    lane = _classify_lane(symbol)

    # First trace boundary — the brain has decided to emit. Anything
    # downstream that doesn't echo this trace_id is the failure point.
    logger.info(
        "[%s] ALPHA_%s_INTENT_CREATED symbol=%s side=%s conf=%.3f exec=%s",
        tid, lane, symbol, raw, final_unit,
        receipt_for_bridge.get("execution_decision", "?"),
    )

    # ── Survival-layer pre-flight ─────────────────────────────────
    # Local mirror of mc_canonical_gate. Builds a runtime envelope
    # (env / git_sha / policy_hash / local_execution_authority=False),
    # runs the kernel, attaches the signed receipt to the outgoing
    # body. The receipt rides on the wire so the broker adapter — in
    # this service or any sibling service — can re-verify.
    envelope = _survival_build_intent(
        brain_id="alpha",
        lane=lane.lower(),
        symbol=symbol,
        direction=raw,
        confidence=final_unit,
        room_id=os.environ.get("RISEDUAL_SIDECAR_ROOM", "alpha"),
    )
    survival_verdict = _survival_gate(envelope)

    if not survival_verdict["accepted"]:
        reason = survival_verdict["reason"]
        if _SURVIVAL_ENFORCE:
            logger.warning(
                "[%s] SURVIVAL_PREFLIGHT_BLOCK lane=%s symbol=%s reason=%s "
                "errors=%s",
                tid, lane, symbol, reason, survival_verdict.get("errors", []),
            )
            return None
        # Soft mode: warn but proceed. Lets us observe the kernel
        # without gating real flow.
        logger.warning(
            "[%s] SURVIVAL_PREFLIGHT_SOFT_DENY lane=%s symbol=%s reason=%s "
            "(set RISEDUAL_SURVIVAL_ENFORCE=1 to hard-block)",
            tid, lane, symbol, reason,
        )
    else:
        logger.info(
            "[%s] SURVIVAL_PREFLIGHT_OK lane=%s symbol=%s policy_hash=%s",
            tid, lane, symbol,
            survival_verdict["receipt"].get("mc_policy_hash", "?")[:8],
        )

    return {
        "symbol": symbol,
        "side": raw,
        "qty": float(qty),
        "confidence": final_unit,
        "notes": notes,
        "trace_id": tid,
        "mc_receipt": survival_verdict["receipt"],
        # ── 2026-05-21 MC prod contract additions ─────────────────
        # The new MC contract uses ``stack`` / ``action`` / ``lane``
        # / ``rationale`` / ``doctrine_snapshot``. We keep the legacy
        # ``side`` / ``notes`` / ``snapshot`` keys above for back-
        # compat during the rollout window — MC ignores unknowns.
        "stack": "alpha",
        "action": raw,
        "lane": lane.lower(),
        "rationale": notes,
        # ── 2026-05-30 MC brain-callable contract additions ──────
        # target_price + stop_price are part of MC's documented
        # minimum body. We derive sane defaults from the consensus
        # receipt: prefer caller-supplied values; otherwise compute
        # from entry_price using a conviction-scaled target band.
        **_derive_price_targets(receipt, raw, final_unit),
        **honesty,
    }


def _derive_price_targets(
    receipt: Mapping[str, Any], direction: str, conf_unit: float,
) -> dict[str, Any]:
    """Best-effort target_price / stop_price derivation.

    Doctrine pin: Alpha is an advisor, not a sizer. We try the
    receipt's explicit fields first; if absent, compute a sensible
    default from ``entry_price`` so the MC gate chain has something
    to score risk:reward against. Returns an empty dict (no fields)
    when no usable price anchor exists — MC accepts the intent
    without the fields, just won't grade R:R.

    Defaults: target = entry ± (1.5% + conf × 3%), stop = entry ∓ 1%.
    Target widens with conviction; stop is fixed-discipline. Same
    posture as the paper-trade closer's 2% trail / 1% stop.
    """
    out: dict[str, Any] = {}
    explicit_target = receipt.get("target_price")
    explicit_stop = receipt.get("stop_price")
    if explicit_target is not None:
        try:
            tp = float(explicit_target)
            if tp > 0 and tp == tp:  # not NaN
                out["target_price"] = tp
        except (TypeError, ValueError):
            pass
    if explicit_stop is not None:
        try:
            sp = float(explicit_stop)
            if sp > 0 and sp == sp:
                out["stop_price"] = sp
        except (TypeError, ValueError):
            pass
    if "target_price" in out and "stop_price" in out:
        return out

    # Derive from entry. Try receipt.entry_price first, then snapshot.price.
    entry = receipt.get("entry_price")
    if entry is None:
        snap = receipt.get("snapshot") or {}
        entry = snap.get("price") if isinstance(snap, Mapping) else None
    try:
        entry_f = float(entry) if entry is not None else None
    except (TypeError, ValueError):
        entry_f = None
    if entry_f is None or entry_f <= 0:
        return out  # no anchor; ship without the fields

    target_band = 0.015 + max(0.0, min(1.0, float(conf_unit))) * 0.03
    stop_band = 0.01
    if direction in ("BUY", "COVER"):
        target = entry_f * (1.0 + target_band)
        stop = entry_f * (1.0 - stop_band)
    else:  # SELL, SHORT
        target = entry_f * (1.0 - target_band)
        stop = entry_f * (1.0 + stop_band)
    out.setdefault("target_price", round(target, 4))
    out.setdefault("stop_price", round(stop, 4))
    return out


def emit_intent_sync(
    client: MCClient,
    receipt: Mapping[str, Any],
    *,
    qty: float = 1.0,
    notes: str = "",
) -> dict[str, Any] | None:
    """Synchronous emit — returns MC's response, ``None`` if skipped.

    Doctrine guard rails apply: non-directional verdicts return None
    without contacting MC. MC errors are re-raised so test code can
    assert on them; the async wrapper below swallows them.
    """
    kwargs = _build_emission_kwargs(receipt, qty=qty, notes=notes)
    if kwargs is None:
        return None
    return client.post_intent(**kwargs)


def _build_opinion_payload(
    receipt: Mapping[str, Any], *, notes: str = "", trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Compose ``post_opinion`` kwargs from a doctrine receipt.

    Unlike the intent path, opinions are emitted for ALL verdicts —
    HOLD / NEUTRAL included. "I have no opinion" is itself a valid
    observation the cross-brain discussion layer wants to see. The
    only thing we refuse is a missing symbol (no anchor → no topic).

    2026-06 wire-up: per operator override, all brains (Alpha included)
    can occupy the executor seat. Opinions ride alongside intents so
    MC's discussion layer surfaces Alpha's reasoning regardless of
    which brain is sitting in the executor seat at the moment.

    2026-06 spec alignment (MC Brain API Quickstart v1 § 4):
      * ``topic`` follows the canonical ``"symbol:<SYMBOL>"`` shape.
      * ``stance`` is drawn from MC's discussion vocabulary
        (``long``/``short``/``observation``/``retract``/…), not the
        raw action verb. Mapping rules:
          BUY   → ``long``       (bullish open thesis)
          SHORT → ``short``      (bearish open thesis)
          HOLD  → ``observation`` (awake, no thesis change)
          SELL / COVER → ``observation`` with body explaining the
              close. We do NOT auto-emit ``retract`` for mechanical
              closes (stop/target/time exits) — promoting every close
              to a retraction pollutes the auditor's "stance change
              vs PnL" surface. Promote to ``retract`` later when we
              track thesis-driven vs mechanical close intent.
    """
    symbol = str(receipt.get("symbol") or "").upper()
    if not symbol:
        return None

    raw = str(
        receipt.get("raw_action") or receipt.get("market_decision") or "HOLD"
    ).upper() or "HOLD"

    final_pct = receipt.get("final_confidence", receipt.get("confidence", 0))
    try:
        final_unit = max(0.0, min(1.0, float(final_pct) / 100.0))
    except (TypeError, ValueError):
        final_unit = 0.5

    lane = _classify_lane(symbol)
    stance = _action_to_stance(raw)
    summary = (
        notes
        or receipt.get("summary")
        or receipt.get("rationale")
        or receipt.get("thesis")
        or f"{raw} {symbol} @ {int(round(final_unit * 100))}% conviction"
    )
    # Make the close-context explicit in the body so an
    # ``observation`` stance on a SELL/COVER isn't ambiguous in the
    # discussion log.
    if raw in {"SELL", "COVER"}:
        summary = f"[{raw} close] {summary}"

    evidence: dict[str, Any] = {
        "lane": lane.lower(),
        "symbol": symbol,
        "raw_action": raw,
        "final_confidence": receipt.get("final_confidence"),
        "raw_confidence": receipt.get("raw_confidence"),
    }
    if trace_id:
        evidence["trace_id"] = trace_id
    snap = receipt.get("snapshot")
    if isinstance(snap, Mapping) and snap:
        evidence["snapshot"] = dict(snap)
    weights = receipt.get("individual_weights")
    if isinstance(weights, Mapping) and weights:
        evidence["individual_weights"] = dict(weights)

    return {
        "topic": f"symbol:{symbol}",
        "stance": stance,
        "body": str(summary),
        "confidence": final_unit,
        "evidence": evidence,
    }


# MC discussion vocabulary mapping. Spec § 4 valid stances:
#   long, short, veto, endorse, question, observation,
#   agree, disagree, refine, retract, hypothesis
_ACTION_TO_STANCE = {
    "BUY": "long",
    "SHORT": "short",
    "HOLD": "observation",
    # SELL/COVER default to ``observation`` — they're executions, not
    # council moves. Promote to ``retract`` only when caller passes
    # an explicit ``in_reply_to`` (thesis-driven close).
    "SELL": "observation",
    "COVER": "observation",
}


def _action_to_stance(action: str) -> str:
    """Map a doctrine action verb to MC's discussion stance vocab.

    Unknown verbs collapse to ``observation`` — safer than guessing
    a directional stance from an unrecognized verb."""
    return _ACTION_TO_STANCE.get((action or "").upper(), "observation")


async def emit_opinion_from_consensus(
    receipt: Mapping[str, Any],
    *,
    notes: str = "alpha consensus tick",
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Fire Alpha's opinion to MC's cross-brain discussion layer.

    Doctrine: opinions are observations, never executions. ``post_opinion``
    forces ``may_execute=False`` on the wire. Best-effort — sidecar
    failures are swallowed inside ``risedual_monorepo_client._post``
    and surface as ``{"ok": False, "error": ...}``.

    Unlike :func:`emit_intent_from_consensus`, this fires on ALL
    verdicts including HOLD — "no opinion" is itself an opinion worth
    publishing to the discussion layer so peer brains can see Alpha
    was awake and chose to stand pat.

    2026-06-09 MC2 wire: when ``RISEDUAL_STANDALONE_MODE=1`` the
    opinion is persisted to MC2's local ``mc2_opinions`` collection
    INSTEAD of POSTing to Original MC. The wire path stays alive in
    code so unsetting the env var snaps back to the legacy flow.
    """
    payload = _build_opinion_payload(receipt, notes=notes, trace_id=trace_id)
    if payload is None:
        return None

    # MC2 wire-in (Phase A — 2026-06-09): when standalone, route the
    # opinion to the local in-process MC2 surface and skip the remote
    # POST entirely. This is the entire severance contract for opinions.
    try:
        from services.mc2 import is_standalone, post_opinion_local
        if is_standalone():
            return await post_opinion_local(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mc2 standalone opinion write failed (non-fatal): symbol=%s err=%s",
            receipt.get("symbol"), exc,
        )
        # Fall through to legacy wire path on MC2 failure — better to
        # ship the opinion to Original MC than drop it entirely.

    try:
        from services import risedual_monorepo_client as _mc_opinion
        return await _mc_opinion.post_opinion(**payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "emit_opinion_from_consensus failed (non-fatal): symbol=%s err=%s",
            receipt.get("symbol"), exc,
        )
        return None


async def emit_intent_from_consensus(
    client: MCClient,
    receipt: Mapping[str, Any],
    *,
    qty: float = 1.0,
    notes: str = "alpha consensus tick",
    emit_opinion: bool = True,
) -> dict[str, Any] | None:
    """Async fire-and-forget wrapper around :func:`emit_intent_sync`.

    This is the one call brain runtimes need to make after a
    consensus tick. Five lines at the call site:

    .. code-block:: python

        from sovereign.intent_bridge import emit_intent_from_consensus
        from sovereign.mc_client import MCClient

        mc = MCClient(base_url=..., brain="alpha", runtime_token=...)
        receipt = await generate_hypothesis(..., model="consensus")
        await emit_intent_from_consensus(mc, receipt)

    MC failures are logged at WARNING and swallowed — the next tick
    will retry on its own. Non-directional verdicts are skipped
    silently and return ``None``.

    2026-05-19 doctrine update: every directional emission is now
    enriched with a normalized market snapshot before going on the
    wire (see ``services.intent_enrichment``). Brains MUST NOT POST
    ``snapshot:{}`` — MC reads missing fields as sentinel values
    and the doctrine score collapses. Enrichment is best-effort and
    always populates the seven canonical keys; sentinel values fill
    in when the upstream quote provider is unavailable.
    """
    # ── Alpha→MC master kill switch (2026-06) ────────────────────────
    # Alpha is standalone; MC has been a dead dependency for months.
    # RISEDUAL_EMIT_INTENTS_TO_MC is the single authoritative switch for
    # the ENTIRE emission surface (remote MC POST *and* the mc2 local
    # rewrite). Set to a falsey value → this chokepoint returns None and
    # NOTHING downstream fires. Unset/truthy → historical behaviour is
    # preserved verbatim, so the code path stays intact for revival.
    _emit_flag = (os.environ.get("RISEDUAL_EMIT_INTENTS_TO_MC") or "").strip().lower()
    if _emit_flag in ("0", "false", "no", "off"):
        logger.info(
            "[intent-bridge] emission disabled (RISEDUAL_EMIT_INTENTS_TO_MC=%s) "
            "— Alpha standalone, MC route severed; skipping symbol=%s",
            _emit_flag, receipt.get("symbol"),
        )
        return None

    # Build the emission kwargs first — non-directional verdicts
    # short-circuit the intent path but still fire an opinion so the
    # discussion layer sees Alpha's reasoning even on HOLD ticks.
    kwargs = _build_emission_kwargs(receipt, qty=qty, notes=notes)
    if kwargs is None:
        if emit_opinion:
            await emit_opinion_from_consensus(receipt, notes=notes)
        return None

    # Enrich with a normalized snapshot. The helper logs a
    # SNAPSHOT_ENRICHED line so the operator can grep emissions and
    # see which carry real data vs sentinel fills.
    try:
        from services.intent_enrichment import enrich_intent_with_snapshot
        kwargs = await enrich_intent_with_snapshot(kwargs)
    except Exception as exc:  # noqa: BLE001
        # Never let the snapshot fetcher block an emission — MC's
        # classifier handles missing snapshots gracefully.
        logger.warning(
            "[%s] SNAPSHOT_ENRICH_FAILED symbol=%s err=%s",
            kwargs.get("trace_id", "--------"),
            kwargs.get("symbol"), exc,
        )

    # MC2 wire-in (Phase A — 2026-06-09): when standalone, persist
    # the intent to the local in-process MC2 surface and skip the
    # remote POST entirely. This is the entire severance contract
    # for intents — same enrichment, same payload shape, different
    # destination. ``client.post_intent`` is NOT called when
    # standalone, so a misconfigured ``RISEDUAL_MC_URL`` no longer
    # blocks Alpha's emission cadence.
    try:
        from services.mc2 import is_standalone, post_intent_local
        if is_standalone():
            result = await post_intent_local(kwargs)
            if emit_opinion:
                try:
                    await emit_opinion_from_consensus(
                        receipt, notes=notes,
                        trace_id=kwargs.get("trace_id"),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "intent_bridge opinion side-channel failed (non-fatal): %s",
                        exc,
                    )
            return result
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mc2 standalone intent write failed (non-fatal): symbol=%s err=%s",
            receipt.get("symbol"), exc,
        )
        # Fall through to legacy wire path so the intent isn't dropped.

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: client.post_intent(**kwargs),
        )
    except MCClientError as exc:
        logger.warning(
            "emit_intent_from_consensus failed (non-fatal): symbol=%s err=%s",
            receipt.get("symbol"), exc,
        )
        result = None

    # Fire the opinion alongside the intent so MC's discussion layer
    # always sees Alpha's reasoning, regardless of whether the intent
    # itself succeeded. Best-effort; never blocks the intent return.
    if emit_opinion:
        try:
            await emit_opinion_from_consensus(
                receipt, notes=notes, trace_id=kwargs.get("trace_id"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "intent_bridge opinion side-channel failed (non-fatal): %s", exc,
            )

    return result


__all__ = [
    "emit_intent_from_consensus",
    "emit_intent_sync",
    "emit_opinion_from_consensus",
]
