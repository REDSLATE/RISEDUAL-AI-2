"""Deterministic, fail-closed majority vote (v8 — RISEDUAL-ready).

This module hosts the single approved tie-broken majority-vote
implementation for any stack that needs to aggregate multiple
classifiers / cores / agents into one decision. It is dependency-
minimal (numpy only).

────────────────────────────────────────────────────────────────────
DOCTRINE
────────────────────────────────────────────────────────────────────

* NO_TRADE wins unsafe / tied / empty cases. Whatever you pass as
  ``no_trade_idx`` is the "safe abstention" class for your domain.
  For trading systems it's literally NO_TRADE. For content
  moderation it might be "manual review." For medical triage it's
  "escalate." Pick the option that defaults to *not acting* and
  ``majority_vote`` will return that whenever the inputs don't
  unambiguously demand action.

* Invalid proposer outputs (out-of-range class indices, NaN
  confidences) are masked at the boundary. They cannot leak into the
  reported average confidence — historical bug in earlier drafts of
  this function: the mask was applied to ``preds`` but not to
  ``confs``, so dropped-prediction confidences still polluted the
  mean. v8 applies the same mask to both arrays.

* All shape / range / dtype mistakes raise ``ValueError`` at the
  boundary. Silent fallthroughs are not allowed. ``n_classes=1``,
  ``no_trade_idx`` outside the class range, length mismatches — all
  caught here rather than producing a wrong but plausible answer
  downstream.

────────────────────────────────────────────────────────────────────
RETURN VALUE
────────────────────────────────────────────────────────────────────

Two-tuple of ``(vote, avg_confidence)``.

* ``vote`` is always a valid class index in ``[0, n_classes)``. It
  is NEVER ``-1`` (some callers use ``-1`` as a "missing data"
  sentinel — that convention is incompatible with this function;
  use ``no_trade_idx`` consistently or wrap the result).
* ``avg_confidence`` is the mean of the *valid* input confidences
  only (post-mask). For empty/invalid input it is ``0.0``.
"""
from __future__ import annotations

import numpy as np


__all__ = ["majority_vote"]


def majority_vote(
    preds,
    confs,
    *,
    n_classes: int,
    no_trade_idx: int,
) -> tuple[int, float]:
    """Tie-broken, fail-closed majority vote.

    Parameters
    ----------
    preds
        Array-like of integer class indices (one per voter).
    confs
        Array-like of float confidences in ``[0, 1]`` (one per voter,
        aligned with ``preds``).
    n_classes
        Total number of classes in the problem. Must be ``>= 2``.
    no_trade_idx
        The class index to return on tie / empty / all-invalid
        input. Must be in ``[0, n_classes)``.

    Returns
    -------
    (vote, avg_conf) : tuple[int, float]

    Raises
    ------
    ValueError
        * ``n_classes < 2``
        * ``no_trade_idx`` outside ``[0, n_classes)``
        * ``len(preds) != len(confs)``

    Notes
    -----
    Filter applies to BOTH preds and confs — invalid class indices
    and NaN confidences die at the same ``valid`` mask, so the
    reported ``avg_conf`` reflects only the voters that actually
    contributed to ``vote``.

    Tie-break order:
      1. If ``no_trade_idx`` is among the tied top classes, return it.
      2. Otherwise return the smallest tied class index.

    This matches the "safe abstention wins ties" doctrine and is
    deterministic across numpy / scipy versions (uses ``np.bincount``,
    not ``scipy.stats.mode``).
    """
    # Config-typo guards — these are about call-site correctness, so
    # they raise unconditionally rather than degrading to a default.
    if n_classes < 2:
        raise ValueError(f"n_classes must be >= 2 (got {n_classes})")
    if not (0 <= no_trade_idx < n_classes):
        raise ValueError(
            f"no_trade_idx must be in [0, {n_classes}) "
            f"(got {no_trade_idx})"
        )

    preds = np.asarray(preds, dtype=int)
    confs = np.asarray(confs, dtype=float)

    if preds.shape[0] != confs.shape[0]:
        raise ValueError(
            f"preds and confs must have the same length "
            f"(got {preds.shape[0]} vs {confs.shape[0]})"
        )

    # Empty input — nothing to vote on, fall through to no_trade_idx.
    if preds.size == 0:
        return no_trade_idx, 0.0

    # Single mask kills three failure modes at the boundary:
    #   * negative class indices (e.g. -1 "no vote" sentinels),
    #   * out-of-range class indices (caller typo / upstream bug),
    #   * NaN / +inf / -inf confidences.
    # Applying it to BOTH preds and confs (v8 bug fix vs v7) ensures
    # avg_conf reports only the voters that actually contributed.
    valid = (preds >= 0) & (preds < n_classes) & np.isfinite(confs)
    preds = preds[valid]
    confs = confs[valid]

    if preds.size == 0:
        return no_trade_idx, 0.0

    counts = np.bincount(preds, minlength=n_classes)
    tied = np.flatnonzero(counts == counts.max())

    vote = no_trade_idx if no_trade_idx in tied else int(tied[0])
    # confs is guaranteed finite post-mask, so plain mean is safe.
    avg_conf = float(np.mean(confs)) if confs.size else 0.0

    return vote, avg_conf
