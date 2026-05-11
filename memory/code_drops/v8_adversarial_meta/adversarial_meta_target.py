"""Adversarial meta-target construction (v8 — RISEDUAL-ready).

This module is the *single source of truth* for building the binary
``y_meta`` target that adversarial / meta-classifier challengers train
against. It is dependency-minimal (numpy only) and intentionally tiny
so any stack — RISEDUAL trading, content moderation, fraud detection,
medical triage — can drop it in without pulling sklearn or pandas.

────────────────────────────────────────────────────────────────────
DOCTRINE (the nine invariants this module enforces or supports)
────────────────────────────────────────────────────────────────────

1. RESOLVED rows only train.
2. PENDING / ERRORED / EXPIRED rows train nothing.
3. ``verified_correct`` defines ``y_meta``.
4. Proposer outputs are features only (see ``challenger_features``).
5. Invalid proposer outputs cannot pollute confidence (see
   ``vote_utils.majority_vote``).
6. Unknown verification states fail closed (this module).
7. NO_TRADE wins unsafe / tied / empty cases (see ``vote_utils``).
8. HOLD cannot be promoted into trade (caller-level invariant).
9. Challenger / Council may reduce or block, never boost (caller-level
   invariant — enforced at the veto-layer authority gate, not here).

────────────────────────────────────────────────────────────────────
WHY ``y_meta`` IS LOCKED TO VERIFIED GROUND TRUTH
────────────────────────────────────────────────────────────────────

Earlier iterations of this design tried to define ``y_meta`` from
proposer state — e.g. ``(majority_vote != y) & (avg_confs > 0.7)``.
That formulation is **non-stationary**: when proposers retrain or
drift, the meta-labels for the SAME historical sample silently flip,
and any "persistence guard" downstream ends up fighting its own
purpose. The v8 reframe defines ``y_meta`` from the **outcome
verification record only** — proposer state goes in as challenger
*features*, never as the label. This makes the meta-target
stationary by definition, and rewriting history becomes impossible.

PENDING rows (verification not yet settled) MUST be excluded from
training entirely — there is no defensible default label for a trade
whose outcome isn't known yet. Treating them as 0, 1, or "drop
silently" all introduce bias. v8's API forces the caller to receive
both ``y_meta`` AND ``resolved_mask`` so the exclusion is a
type-shape contract rather than a docstring promise: skipping the
mask raises a shape error downstream, not a silent correctness bug.
"""
from __future__ import annotations

import numpy as np


# ── Verification-status enum (string constants for ergonomic Mongo /
#    JSON interop; sets for membership checks). ───────────────────────

PENDING = "PENDING"
RESOLVED = "RESOLVED"
ERRORED = "ERRORED"
EXPIRED = "EXPIRED"

#: Every status this module recognises. Anything not in this set is
#: a typo or a new state that has not yet been doctrinally classified
#: as trainable / non-trainable — ``normalize_statuses`` raises so the
#: operator can decide explicitly.
VALID_STATUSES = frozenset({PENDING, RESOLVED, ERRORED, EXPIRED})

#: Statuses that exist but MUST NOT enter the training set. Used by
#: callers that want to surface "row count by lane" telemetry without
#: re-deriving the partition rule.
NON_TRAINABLE_STATUSES = frozenset({PENDING, ERRORED, EXPIRED})


__all__ = [
    "PENDING",
    "RESOLVED",
    "ERRORED",
    "EXPIRED",
    "VALID_STATUSES",
    "NON_TRAINABLE_STATUSES",
    "normalize_statuses",
    "build_verified_veto_target",
]


def normalize_statuses(verification_status) -> np.ndarray:
    """Coerce an arbitrary status iterable into a validated numpy
    string array.

    Behaviour:
      * Accepts any array-like of values that can be cast to ``str``
        (e.g. ``["resolved", "PENDING", "Expired"]`` works fine).
      * Upper-cases every entry so callers don't have to worry about
        case-folding at the DB boundary.
      * **Fails closed** on any value not in :data:`VALID_STATUSES`.
        This is doctrinal: unknown states are an explicit operator
        decision, not a silent fallthrough to "PENDING".

    Parameters
    ----------
    verification_status
        1-D array-like of status strings.

    Returns
    -------
    np.ndarray
        Upper-cased, validated 1-D string array of the same length.

    Raises
    ------
    ValueError
        If any status value (after upper-casing) is not a member of
        :data:`VALID_STATUSES`.
    """
    status = np.asarray(verification_status).astype(str)
    status = np.char.upper(status)
    unknown = set(status.tolist()) - VALID_STATUSES
    if unknown:
        raise ValueError(
            f"Unknown verification_status values: {sorted(unknown)}. "
            f"Allowed: {sorted(VALID_STATUSES)}. "
            f"Add the new status to VALID_STATUSES and "
            f"NON_TRAINABLE_STATUSES (or document it as RESOLVED) "
            f"BEFORE retraining."
        )
    return status


def build_verified_veto_target(
    verified_correct,
    verification_status,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the binary meta-classifier target from outcome verification.

    Contract
    --------
    * Only ``RESOLVED`` rows enter ``y_meta``. PENDING / ERRORED /
      EXPIRED rows are dropped via ``resolved_mask``.
    * ``y_meta = 1`` means the accepted decision was later **proven
      wrong**. ``y_meta = 0`` means it was proven correct.
    * Proposer votes, proposer confidence, and any other model
      outputs are NEVER part of label construction. Pass them in as
      features (see :func:`challenger_features.build_challenger_features`),
      not as labels.
    * A ``RESOLVED`` row with ``verified_correct=None`` is a data
      integrity bug at the verification pipeline, not a valid label
      — this function raises rather than guessing.

    Parameters
    ----------
    verified_correct
        1-D array-like aligned with ``verification_status``. Each
        entry is ``True`` / ``False`` for RESOLVED rows and may be
        ``None`` for non-RESOLVED rows (where it is masked out and
        ignored). Stored as dtype=object internally so ``None``
        values are preserved through the mask.
    verification_status
        1-D array-like of status strings (case-insensitive). Must
        contain only members of :data:`VALID_STATUSES`.

    Returns
    -------
    y_meta : np.ndarray of shape ``(n_resolved,)``, dtype int
        ``1`` where the accepted decision was wrong, ``0`` where it
        was right. Length equals ``resolved_mask.sum()``, NOT the
        original input length.
    resolved_mask : np.ndarray of shape ``(n_total,)``, dtype bool
        Caller MUST apply this mask to every feature array, prediction
        array, weight array, etc. aligned with the original inputs
        before passing them to model fit. The length mismatch between
        ``y_meta`` and the original inputs is intentional — it
        converts "forgot to filter pending rows" from a silent
        correctness bug into a loud numpy shape error.

    Raises
    ------
    ValueError
        * Mismatched input lengths.
        * Unknown verification_status value (via
          :func:`normalize_statuses`).
        * RESOLVED row with ``verified_correct is None``.
    """
    # dtype=object preserves None values so the explicit None-check
    # below can fire. A plain np.asarray(...) on a list with None
    # mixed in produces dtype=object naturally; we force it for
    # clarity and so future callers passing a pre-typed array don't
    # accidentally lose None values to coercion.
    verified = np.asarray(verified_correct, dtype=object)
    status = normalize_statuses(verification_status)

    if len(verified) != len(status):
        raise ValueError(
            f"verified_correct and verification_status must align "
            f"(got len(verified_correct)={len(verified)} vs "
            f"len(verification_status)={len(status)})"
        )

    resolved_mask = (status == RESOLVED)
    resolved_verified = verified[resolved_mask]

    # Numpy arrays need elementwise comparison; `is None` does not broadcast.
    if np.any(resolved_verified == None):  # noqa: E711
        raise ValueError(
            "RESOLVED rows cannot have verified_correct=None. "
            "This indicates a data integrity bug in the verification "
            "pipeline — a row was marked RESOLVED before its outcome "
            "was actually written. Fix at the verifier, not here."
        )

    resolved_verified = resolved_verified.astype(bool)
    y_meta = (~resolved_verified).astype(int)
    return y_meta, resolved_mask
