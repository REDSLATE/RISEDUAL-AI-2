"""v8 doctrine regression tests.

Every invariant the eight-iteration design arc surfaced is pinned
here. If a future maintainer "refactors" any of these primitives and
the test fails, the framework has regressed to an earlier (broken)
iteration — DO NOT loosen the assertion; fix the code.

Run::

    pytest tests/test_v8_doctrine.py -v

Or, if you've dropped the folder somewhere on PYTHONPATH::

    python -m pytest path/to/v8_adversarial_meta/tests
"""
from __future__ import annotations

import numpy as np
import pytest

from v8_adversarial_meta import (
    ERRORED,
    EXPIRED,
    NON_TRAINABLE_STATUSES,
    PENDING,
    RESOLVED,
    VALID_STATUSES,
    build_challenger_features,
    build_verified_veto_target,
    majority_vote,
    normalize_statuses,
)


# ════════════════════════════════════════════════════════════════════
# Status enum
# ════════════════════════════════════════════════════════════════════


def test_status_enum_constants_match_strings():
    assert PENDING == "PENDING"
    assert RESOLVED == "RESOLVED"
    assert ERRORED == "ERRORED"
    assert EXPIRED == "EXPIRED"


def test_valid_statuses_membership():
    assert VALID_STATUSES == frozenset({"PENDING", "RESOLVED", "ERRORED", "EXPIRED"})


def test_non_trainable_statuses_excludes_resolved():
    """Only RESOLVED rows train; all other valid statuses are
    non-trainable. Doctrine invariants 1 & 2."""
    assert RESOLVED not in NON_TRAINABLE_STATUSES
    assert NON_TRAINABLE_STATUSES == frozenset({"PENDING", "ERRORED", "EXPIRED"})


def test_normalize_statuses_uppercases():
    out = normalize_statuses(["resolved", "Pending", "EXPIRED"])
    assert out.tolist() == ["RESOLVED", "PENDING", "EXPIRED"]


def test_normalize_statuses_fails_closed_on_unknown():
    """Doctrine invariant 6: unknown verification states fail closed."""
    with pytest.raises(ValueError, match="Unknown verification_status"):
        normalize_statuses(["RESOLVED", "MAYBE", "PENDING"])


# ════════════════════════════════════════════════════════════════════
# build_verified_veto_target
# ════════════════════════════════════════════════════════════════════


def test_pending_rows_are_excluded_from_meta_training():
    """v7 load-bearing test. Doctrine invariants 1 & 2."""
    verified_correct = np.array([True, False, False, True])
    verification_status = np.array(["RESOLVED", "PENDING", "RESOLVED", "PENDING"])
    y_meta, mask = build_verified_veto_target(verified_correct, verification_status)
    assert mask.tolist() == [True, False, True, False]
    assert y_meta.tolist() == [0, 1]


def test_errored_and_expired_rows_are_also_excluded():
    """All three non-RESOLVED statuses must be excluded uniformly."""
    verified_correct = [True, True, True, False]
    verification_status = ["RESOLVED", "ERRORED", "EXPIRED", "RESOLVED"]
    y_meta, mask = build_verified_veto_target(verified_correct, verification_status)
    assert mask.tolist() == [True, False, False, True]
    assert y_meta.tolist() == [0, 1]


def test_y_meta_invariant_to_proposer_drift():
    """v6 load-bearing test. The function literally never sees
    proposer state, so y_meta cannot drift when proposers do.
    Doctrine invariants 3 & 4."""
    verified_correct = np.array([True, False, True, False, False])
    verification_status = np.array([RESOLVED] * 5)
    y_meta_v1, _ = build_verified_veto_target(verified_correct, verification_status)
    # Simulate completely different proposer outputs (which the
    # function never sees).
    _proposer_votes_v1 = np.array([0, 1, 2, 3, 0])  # noqa: F841
    _proposer_votes_v2 = np.array([3, 3, 3, 1, 2])  # noqa: F841
    y_meta_v2, _ = build_verified_veto_target(verified_correct, verification_status)
    assert np.array_equal(y_meta_v1, y_meta_v2)


def test_lowercase_status_normalised():
    verified_correct = [True, False]
    verification_status = ["resolved", "resolved"]
    y_meta, mask = build_verified_veto_target(verified_correct, verification_status)
    assert mask.tolist() == [True, True]
    assert y_meta.tolist() == [0, 1]


def test_resolved_row_with_none_verified_raises():
    """Doctrine: a RESOLVED row with None verified_correct is a data
    integrity bug. Fail loud, don't guess."""
    verified_correct = [True, None, False]
    verification_status = ["RESOLVED", "RESOLVED", "RESOLVED"]
    with pytest.raises(ValueError, match="cannot have verified_correct=None"):
        build_verified_veto_target(verified_correct, verification_status)


def test_pending_row_with_none_verified_is_fine():
    """None is permitted on non-RESOLVED rows because they're
    masked out before the bool cast."""
    verified_correct = [True, None, False]
    verification_status = ["RESOLVED", "PENDING", "RESOLVED"]
    y_meta, mask = build_verified_veto_target(verified_correct, verification_status)
    assert mask.tolist() == [True, False, True]
    assert y_meta.tolist() == [0, 1]


def test_misaligned_lengths_raise():
    with pytest.raises(ValueError, match="must align"):
        build_verified_veto_target(
            verified_correct=[True, False, True],
            verification_status=[RESOLVED, RESOLVED],
        )


def test_all_pending_returns_empty_y_meta():
    """No RESOLVED rows → empty y_meta, all-False mask. Caller must
    handle this (don't train on zero rows)."""
    y_meta, mask = build_verified_veto_target(
        verified_correct=[None, None, None],
        verification_status=[PENDING, PENDING, PENDING],
    )
    assert mask.tolist() == [False, False, False]
    assert y_meta.tolist() == []


# ════════════════════════════════════════════════════════════════════
# majority_vote
# ════════════════════════════════════════════════════════════════════


def test_majority_vote_simple_majority():
    vote, conf = majority_vote([0, 0, 1], [0.8, 0.9, 0.7], n_classes=3, no_trade_idx=2)
    assert vote == 0
    assert conf == pytest.approx((0.8 + 0.9 + 0.7) / 3)


def test_majority_vote_tie_resolves_to_no_trade_when_no_trade_is_candidate():
    """Doctrine invariant 7: NO_TRADE wins ties — but per the v8
    code contract, only when NO_TRADE is itself one of the tied
    top classes. See ``test_majority_vote_tie_without_no_trade...``
    for the complementary case."""
    # 3-way tie between classes 0, 1, NO_TRADE(3) → NO_TRADE wins.
    vote, _ = majority_vote(
        [0, 1, 3], [0.5, 0.5, 0.5], n_classes=5, no_trade_idx=3,
    )
    assert vote == 3


def test_majority_vote_tie_without_no_trade_picks_smallest_index():
    """Tie between two non-NO_TRADE classes → smallest index wins.
    Deterministic across runs / platforms / numpy versions.

    Doctrinal note: invariant 7 says "NO_TRADE wins tied cases" but
    the v8 code only enforces that when NO_TRADE is itself in the
    tie. A 2v2 directional split like [LONG, LONG, SHORT, SHORT]
    currently resolves to LONG (smallest index), NOT NO_TRADE. If
    that's not the intended doctrine, change the code AND this
    test together — never just the test."""
    vote, _ = majority_vote([0, 0, 2, 2], [0.5, 0.5, 0.5, 0.5], n_classes=5, no_trade_idx=3)
    assert vote == 0


def test_majority_vote_empty_input_returns_no_trade():
    """Doctrine invariant 7: empty case → NO_TRADE."""
    vote, conf = majority_vote([], [], n_classes=3, no_trade_idx=2)
    assert vote == 2
    assert conf == 0.0


def test_majority_vote_filters_invalid_class_indices():
    """Invalid (negative or out-of-range) class indices are masked
    out before counting."""
    vote, _ = majority_vote([0, 0, -1, 99], [0.7, 0.8, 0.9, 0.6], n_classes=5, no_trade_idx=3)
    assert vote == 0


def test_majority_vote_confs_filter_applied_with_preds():
    """v8 bug-1 regression: confs MUST be filtered alongside preds.
    Earlier drafts left confs un-masked, polluting avg_conf with
    confidences attached to invalid predictions.

    Setup: preds=[0, 1, -1, 99], confs=[0.7, 0.8, 0.9, 0.6].
    After the valid mask, only preds[0,1] = [0,1] survive, with
    confs [0.7, 0.8]. avg_conf should be 0.75, NOT (0.7+0.8+0.9+0.6)/4 = 0.75
    by coincidence — let's use values that distinguish them."""
    vote, conf = majority_vote(
        [0, 1, -1, 99],
        [0.7, 0.8, 0.1, 0.2],  # bad-pred confs are LOW
        n_classes=5,
        no_trade_idx=3,
    )
    # vote is a tie between class 0 and class 1; NO_TRADE NOT in
    # tie → smallest-index wins per v8 contract.
    assert vote == 0
    # avg_conf should be mean([0.7, 0.8]) = 0.75, NOT
    # mean([0.7, 0.8, 0.1, 0.2]) = 0.45.
    assert conf == pytest.approx(0.75)


def test_majority_vote_nan_confs_filtered():
    """NaN confidence is dropped at the same mask as invalid preds.
    Vote falls through to smallest of the surviving preds; the
    load-bearing assertion is that NaN doesn't pollute avg_conf."""
    vote, conf = majority_vote(
        [0, 0, 1],
        [0.8, np.nan, 0.6],
        n_classes=3,
        no_trade_idx=2,
    )
    # preds=[0,0,1] after NaN mask on idx-1 → preds=[0,1],
    # confs=[0.8, 0.6]. Tie between 0 and 1, NO_TRADE (2) not in
    # tie → smallest-index wins.
    assert vote == 0
    assert conf == pytest.approx((0.8 + 0.6) / 2)


def test_majority_vote_inf_confs_filtered():
    """+inf / -inf confidences also fail np.isfinite check and are
    dropped at the boundary."""
    vote, conf = majority_vote(
        [0, 0, 1],
        [0.8, np.inf, 0.6],
        n_classes=3,
        no_trade_idx=2,
    )
    # Same shape as NaN test — surviving preds [0,1] tie, no
    # NO_TRADE in tie, smallest wins.
    assert vote == 0
    assert conf == pytest.approx((0.8 + 0.6) / 2)


def test_majority_vote_all_invalid_returns_no_trade():
    """If every input is invalid (all NaN confs, all bad preds, or
    a mix), function falls through to no_trade_idx."""
    vote, conf = majority_vote(
        [-1, -1, 99],
        [np.nan, np.nan, np.nan],
        n_classes=3,
        no_trade_idx=2,
    )
    assert vote == 2
    assert conf == 0.0


def test_majority_vote_raises_on_n_classes_too_small():
    with pytest.raises(ValueError, match="n_classes must be >= 2"):
        majority_vote([0], [0.5], n_classes=1, no_trade_idx=0)


def test_majority_vote_raises_on_no_trade_idx_out_of_range():
    with pytest.raises(ValueError, match="no_trade_idx must be"):
        majority_vote([0, 1], [0.5, 0.5], n_classes=3, no_trade_idx=99)


def test_majority_vote_raises_on_mismatched_lengths():
    with pytest.raises(ValueError, match="same length"):
        majority_vote([0, 1, 2], [0.5, 0.5], n_classes=3, no_trade_idx=2)


def test_majority_vote_never_returns_negative_sentinel():
    """``-1`` as a "no vote" sentinel is incompatible with this
    function. Empty / all-invalid input returns ``no_trade_idx``,
    not ``-1``."""
    vote, _ = majority_vote([], [], n_classes=5, no_trade_idx=4)
    assert vote == 4
    assert vote != -1


# ════════════════════════════════════════════════════════════════════
# build_challenger_features
# ════════════════════════════════════════════════════════════════════


def test_build_challenger_features_concatenates_columns():
    base_X = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    out = build_challenger_features(
        base_X=base_X,
        majority_votes=[0, 1, 2],
        avg_confs=[0.5, 0.7, 0.9],
        disagreement_rate=[0.0, 0.3, 0.5],
    )
    assert out.shape == (3, 5)
    # Last three columns are the proposer-state columns in order.
    np.testing.assert_array_equal(out[:, -3], [0, 1, 2])
    np.testing.assert_allclose(out[:, -2], [0.5, 0.7, 0.9])
    np.testing.assert_allclose(out[:, -1], [0.0, 0.3, 0.5])
    # Base columns are preserved.
    np.testing.assert_array_equal(out[:, :2], base_X)


def test_build_challenger_features_rejects_mismatched_majority_votes():
    with pytest.raises(ValueError, match="majority_votes length"):
        build_challenger_features(
            base_X=np.zeros((3, 2)),
            majority_votes=[0, 1],  # wrong length
            avg_confs=[0.5, 0.5, 0.5],
            disagreement_rate=[0.0, 0.0, 0.0],
        )


def test_build_challenger_features_rejects_mismatched_avg_confs():
    with pytest.raises(ValueError, match="avg_confs length"):
        build_challenger_features(
            base_X=np.zeros((3, 2)),
            majority_votes=[0, 1, 2],
            avg_confs=[0.5, 0.5],  # wrong length
            disagreement_rate=[0.0, 0.0, 0.0],
        )


def test_build_challenger_features_rejects_mismatched_disagreement():
    with pytest.raises(ValueError, match="disagreement_rate length"):
        build_challenger_features(
            base_X=np.zeros((3, 2)),
            majority_votes=[0, 1, 2],
            avg_confs=[0.5, 0.5, 0.5],
            disagreement_rate=[0.0, 0.0],  # wrong length
        )


# ════════════════════════════════════════════════════════════════════
# End-to-end caller protocol
# ════════════════════════════════════════════════════════════════════


def test_end_to_end_mask_applied_after_feature_assembly():
    """The doctrine-prescribed caller protocol: build features for
    ALL rows, then apply ``resolved_mask`` before fit. y_meta length
    must match the masked X length."""
    base_X = np.array([[1, 1], [2, 2], [3, 3], [4, 4], [5, 5]], dtype=float)
    verified_correct = [True, None, False, None, True]
    verification_status = [RESOLVED, PENDING, RESOLVED, PENDING, RESOLVED]
    majority_votes = [0, 1, 2, 0, 1]
    avg_confs = [0.7, 0.5, 0.9, 0.4, 0.8]
    disagreement_rate = [0.0, 0.5, 0.2, 0.7, 0.1]

    y_meta, resolved_mask = build_verified_veto_target(
        verified_correct, verification_status,
    )
    X_full = build_challenger_features(
        base_X=base_X,
        majority_votes=majority_votes,
        avg_confs=avg_confs,
        disagreement_rate=disagreement_rate,
    )
    X_train = X_full[resolved_mask]

    # Length contract: y_meta and the masked feature matrix agree.
    assert X_train.shape[0] == y_meta.shape[0]
    assert X_train.shape == (3, 5)
    # Verify only RESOLVED rows survived.
    np.testing.assert_array_equal(X_train[:, 0], [1, 3, 5])  # base_X col 0
    assert y_meta.tolist() == [0, 1, 0]


def test_end_to_end_skipping_mask_produces_shape_error():
    """The whole point of returning the mask: forgetting to apply
    it must crash, not silently misalign."""
    verified_correct = [True, None, False]
    verification_status = [RESOLVED, PENDING, RESOLVED]
    y_meta, _ = build_verified_veto_target(verified_correct, verification_status)

    # Simulate a forgetful caller passing the full-length X.
    X_full_length = np.zeros((3, 4))
    # In real sklearn this would raise ValueError; we just check the
    # shape mismatch surfaces, which is the API's safety property.
    assert X_full_length.shape[0] != y_meta.shape[0]
