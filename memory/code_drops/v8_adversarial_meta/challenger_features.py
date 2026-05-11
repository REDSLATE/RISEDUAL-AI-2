"""Challenger feature assembly (v8 — RISEDUAL-ready).

Stitches base features together with proposer-state columns
(``majority_votes``, ``avg_confs``, ``disagreement_rate``) into the
matrix consumed by the binary meta-classifier challengers.

────────────────────────────────────────────────────────────────────
DOCTRINE
────────────────────────────────────────────────────────────────────

Proposer outputs are FEATURES, not labels. The challenger sees
proposer state as input columns and learns where high-confidence
proposers tend to fail. This replaces the v5-and-earlier approach of
hardcoding a magic ``TOXIC_CONFIDENCE_THRESHOLD`` constant — the
challenger discovers the boundary from data rather than being told.

The label is built separately by
:func:`adversarial_meta_target.build_verified_veto_target` and is
based ONLY on verified outcomes — proposer state never enters the
label. That separation is what makes the meta-target stationary
under proposer drift.

────────────────────────────────────────────────────────────────────
INTEGRATION NOTE
────────────────────────────────────────────────────────────────────

This function does NOT apply the ``resolved_mask`` returned by
``build_verified_veto_target``. The expected caller protocol is::

    y_meta, resolved_mask = build_verified_veto_target(
        verified_correct=df["verified_correct"].values,
        verification_status=df["verification_status"].values,
    )
    X_full = build_challenger_features(
        base_X=base_features,
        majority_votes=mv,
        avg_confs=ac,
        disagreement_rate=dr,
    )
    X_train = X_full[resolved_mask]   # ← apply mask AFTER assembly
    challenger.fit(X_train, y_meta)

Building features for the full set first (then masking) keeps this
function single-purpose and lets the caller use the same feature
matrix for both training (masked) and live inference (unmasked).
"""
from __future__ import annotations

import numpy as np


__all__ = ["build_challenger_features"]


def build_challenger_features(
    base_X,
    majority_votes,
    avg_confs,
    disagreement_rate,
) -> np.ndarray:
    """Concatenate base features with proposer-state columns.

    Parameters
    ----------
    base_X : array-like of shape (n_samples, n_base_features)
        The underlying feature matrix (market features, regime
        fingerprint, macro indicators, etc.).
    majority_votes : array-like of shape (n_samples,)
        Integer class index voted by the proposer ensemble for each
        sample.
    avg_confs : array-like of shape (n_samples,)
        Mean proposer confidence in ``[0, 1]`` for each sample.
    disagreement_rate : array-like of shape (n_samples,)
        Fraction of proposers that voted against the majority for
        each sample. ``0.0`` = unanimous, higher = more contentious.

    Returns
    -------
    np.ndarray of shape (n_samples, n_base_features + 3)
        Column order: ``[*base_X, majority_votes, avg_confs,
        disagreement_rate]``.

    Raises
    ------
    ValueError
        If any column-vector length does not match ``base_X.shape[0]``.
        Failing loud at assembly is doctrinally preferable to silent
        misalignment downstream — the row counts MUST agree for the
        feature matrix to be coherent.
    """
    base_X = np.asarray(base_X)
    majority_votes = np.asarray(majority_votes)
    avg_confs = np.asarray(avg_confs)
    disagreement_rate = np.asarray(disagreement_rate)

    n = base_X.shape[0]

    if majority_votes.shape[0] != n:
        raise ValueError(
            f"majority_votes length must match base_X rows "
            f"(got {majority_votes.shape[0]} vs {n})"
        )
    if avg_confs.shape[0] != n:
        raise ValueError(
            f"avg_confs length must match base_X rows "
            f"(got {avg_confs.shape[0]} vs {n})"
        )
    if disagreement_rate.shape[0] != n:
        raise ValueError(
            f"disagreement_rate length must match base_X rows "
            f"(got {disagreement_rate.shape[0]} vs {n})"
        )

    return np.column_stack([
        base_X,
        majority_votes,
        avg_confs,
        disagreement_rate,
    ])
