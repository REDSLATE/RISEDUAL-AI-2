"""
CACL — Confusion-Aware Contrastive Learning (Phase 1, pure NumPy).

Patent M layer (a) — embedding/confusion engine.

Why pure NumPy
--------------
Phase 1 deliberately avoids PyTorch/TF. We want:
* deterministic outputs given a seed (test stability),
* zero new heavyweight deps in the pod,
* a small, auditable surface so the IP claim ("confusion-aware
  contrastive learning + hard-negative mining + prototype memory
  bank") is provable from the source itself, not from a framework.

What this module does
---------------------
1. Projects raw feature vectors → L2-normalised embeddings via a
   single linear matrix ``W``.
2. Maintains per-class prototype vectors ``P[c]``. Prototypes are
   the running mean of embeddings labelled ``c`` (a memory bank
   with EMA decay).
3. ``predict_proba`` = softmax over cosine similarity between the
   embedding and every prototype. (Cosine, not Euclidean —
   prototypes drift in magnitude during training; cosine is
   scale-stable.)
4. ``training_step`` does three things in one pass:
     a. updates the prototypes toward each labelled example,
     b. mines the hardest negative prototype (closest non-label
        prototype) and pushes it away,
     c. accumulates a confusion matrix of (true_label,
        predicted_label) so the operator can see *what* the model
        keeps mistaking — that is the "confusion-aware" half of
        CACL.

What this module deliberately does NOT do
-----------------------------------------
* No order placement, no broker calls, no Mongo writes.
* No direction canonicalisation (that's the orchestrator's job).
* No regime awareness (that's the regime tagger + memory
  retriever's job).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


# ─── Hyperparameters (kept as module-level constants so a future
# admin route can render them read-only without bytecode-walking) ──

DEFAULT_EMBED_DIM = 128
DEFAULT_PROTO_LR = 0.10          # how fast prototypes track new examples
DEFAULT_HARD_NEG_PUSH = 0.05     # repulsion strength for hard negatives
DEFAULT_TEMPERATURE = 0.10       # softmax temperature for predict_proba
DEFAULT_SEED = 7                 # deterministic init for tests


@dataclass
class TrainingStepResult:
    """Per-batch diagnostic envelope.

    Surfaced so tests can assert that training actually moved
    something, and so a future admin tile can render "what did the
    last batch teach the model".
    """

    batch_size: int
    mean_loss: float
    accuracy: float
    proto_drift: float            # L2 norm of prototype delta
    hardest_confusion: Optional[Dict[str, Any]] = None
    confusion_matrix: List[List[int]] = field(default_factory=list)


class ConfusionAwareEmbeddingNetwork:
    """Pure-NumPy CACL embedding + prototype network."""

    def __init__(
        self,
        input_dim: int,
        embedding_dim: int = DEFAULT_EMBED_DIM,
        n_prototypes: int = 3,
        proto_lr: float = DEFAULT_PROTO_LR,
        hard_neg_push: float = DEFAULT_HARD_NEG_PUSH,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int = DEFAULT_SEED,
    ) -> None:
        if input_dim <= 0:
            raise ValueError("input_dim must be > 0")
        if embedding_dim <= 0:
            raise ValueError("embedding_dim must be > 0")
        if n_prototypes < 2:
            raise ValueError("n_prototypes must be >= 2")

        self.input_dim = input_dim
        self.embedding_dim = embedding_dim
        self.n_prototypes = n_prototypes
        self.proto_lr = float(proto_lr)
        self.hard_neg_push = float(hard_neg_push)
        self.temperature = float(temperature)

        rng = np.random.default_rng(seed)
        # Xavier-ish init keeps the first batch's embeddings inside
        # roughly [-1, 1] so the cosine sims are well-conditioned
        # before any training has happened.
        scale = 1.0 / np.sqrt(input_dim)
        self.W = rng.normal(0.0, scale, size=(input_dim, embedding_dim))

        # Prototypes start on the unit sphere, evenly spread by a
        # random orthogonal-ish init. We normalise after every update.
        proto_init = rng.normal(0.0, 1.0, size=(n_prototypes, embedding_dim))
        self.P = self._row_normalize(proto_init)

        # Confusion matrix: rows = true label, cols = predicted label.
        self.confusion_matrix = np.zeros(
            (n_prototypes, n_prototypes), dtype=np.int64,
        )

        # Memory-bank size hint: how many examples the running
        # prototypes have seen. Used to slow proto_lr over time so
        # later batches can't blow away early structure.
        self._n_seen = np.zeros(n_prototypes, dtype=np.int64)

    # ─── public ─────────────────────────────────────────────────

    def embed(self, X: np.ndarray) -> np.ndarray:
        """Project ``X`` (shape ``[N, input_dim]``) to L2-normalised
        embeddings (shape ``[N, embedding_dim]``)."""
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        if X.shape[1] != self.input_dim:
            raise ValueError(
                f"expected feature dim {self.input_dim}, got {X.shape[1]}"
            )
        return self._row_normalize(X @ self.W)

    def predict_proba(self, embedding: np.ndarray) -> np.ndarray:
        """Softmax over cosine-similarity to every prototype.

        Accepts either a single embedding (shape ``[D]``) or a batch
        (shape ``[N, D]``). Returns ``[N, n_prototypes]`` always.
        """
        emb = np.asarray(embedding, dtype=float)
        if emb.ndim == 1:
            emb = emb.reshape(1, -1)
        # emb is L2-normalised by ``embed``; prototypes are also
        # L2-normalised. So @ is exactly cosine similarity.
        sims = emb @ self.P.T
        # Stable softmax with temperature.
        z = sims / max(self.temperature, 1e-6)
        z = z - z.max(axis=1, keepdims=True)
        ez = np.exp(z)
        return ez / ez.sum(axis=1, keepdims=True)

    def training_step(
        self,
        features: np.ndarray,
        labels: np.ndarray,
    ) -> Dict[str, Any]:
        """Single-batch update.

        Updates:
        * prototypes (EMA toward labelled examples),
        * hardest-negative repulsion,
        * confusion matrix.

        Returns a ``TrainingStepResult`` envelope as a plain dict
        (so it serialises cleanly into admin diagnostics).
        """
        X = np.asarray(features, dtype=float)
        y = np.asarray(labels, dtype=np.int64).reshape(-1)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        if X.shape[0] != y.shape[0]:
            raise ValueError("features and labels length mismatch")
        if y.min() < 0 or y.max() >= self.n_prototypes:
            raise ValueError(
                f"labels must be in [0, {self.n_prototypes - 1}]"
            )

        emb = self.embed(X)
        proba = self.predict_proba(emb)
        preds = proba.argmax(axis=1)

        # Confusion matrix update + accuracy.
        for true_lbl, pred_lbl in zip(y, preds):
            self.confusion_matrix[true_lbl, pred_lbl] += 1
        accuracy = float((preds == y).mean())

        # Per-class running prototype update (EMA toward batch mean
        # of that class's embeddings).
        proto_before = self.P.copy()
        loss_terms: List[float] = []

        for cls in np.unique(y):
            mask = (y == cls)
            cls_emb_mean = emb[mask].mean(axis=0)

            # EMA decay: proto_lr / (1 + n_seen / 50). This is the
            # "memory bank with adaptive learning rate" half of the
            # CACL claim — early examples teach a lot, later ones
            # only refine.
            n_seen = int(self._n_seen[cls])
            lr = self.proto_lr / (1.0 + n_seen / 50.0)
            self.P[cls] = (1.0 - lr) * self.P[cls] + lr * cls_emb_mean
            self._n_seen[cls] += int(mask.sum())

            # Hardest-negative mining: which non-true prototype is
            # closest (= most confusing) for this batch's class?
            sims_to_protos = cls_emb_mean @ self.P.T
            sims_to_protos[cls] = -np.inf
            hard_neg = int(np.argmax(sims_to_protos))
            # Push the hard-negative prototype away from the class
            # mean (small step, bounded). This is the "adaptive
            # contrastive loss" half of the CACL claim.
            self.P[hard_neg] = (
                self.P[hard_neg] - self.hard_neg_push * cls_emb_mean
            )

            # Loss surrogate: 1 - cosine(emb, P[cls]).
            loss_terms.append(
                float(1.0 - (emb[mask] @ self.P[cls]).mean())
            )

        # Re-normalise prototypes so cosine math stays clean.
        self.P = self._row_normalize(self.P)

        proto_drift = float(np.linalg.norm(self.P - proto_before))
        mean_loss = float(np.mean(loss_terms)) if loss_terms else 0.0

        # Hardest current confusion across the whole confusion
        # matrix — what does the model keep mistaking?
        hardest = self._hardest_confusion()

        result = TrainingStepResult(
            batch_size=int(X.shape[0]),
            mean_loss=mean_loss,
            accuracy=accuracy,
            proto_drift=proto_drift,
            hardest_confusion=hardest,
            confusion_matrix=self.confusion_matrix.tolist(),
        )
        return result.__dict__

    def reset_confusion(self) -> None:
        """Operator escape hatch — start a fresh confusion window
        without touching prototype weights."""
        self.confusion_matrix = np.zeros_like(self.confusion_matrix)

    # ─── internal ───────────────────────────────────────────────

    @staticmethod
    def _row_normalize(M: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        norms = np.where(norms < 1e-12, 1.0, norms)
        return M / norms

    def _hardest_confusion(self) -> Optional[Dict[str, Any]]:
        """Return the (true, pred) pair with the largest off-diagonal
        count, or ``None`` if no off-diagonal mistakes exist yet.
        """
        cm = self.confusion_matrix
        off = cm.copy()
        np.fill_diagonal(off, 0)
        if off.sum() == 0:
            return None
        idx = int(np.argmax(off))
        true_lbl, pred_lbl = divmod(idx, cm.shape[1])
        return {
            "true_label": int(true_lbl),
            "predicted_label": int(pred_lbl),
            "count": int(off[true_lbl, pred_lbl]),
        }
