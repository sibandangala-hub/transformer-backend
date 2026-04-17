"""
ml/autoencoder.py — Sklearn-based Autoencoder (Phase 1: Unsupervised).

Architecture (symmetric encoder-decoder):
    19 → 14 → 8 → 5 → 8 → 14 → 19

Trained exclusively on NORMAL readings.
Anomaly score = reconstruction MSE.
    score ≥ threshold_warn → WARNING  (severity = 1)
    score ≥ threshold_crit → CRITICAL (severity = 2)

Why sklearn MLPRegressor instead of Keras/TensorFlow?
  • ~50 MB install vs ~500 MB for TF — fits Render free tier (512 MB RAM)
  • No GPU required; training on 50-500 samples is sub-second on CPU
  • Full sklearn serialisation via pickle
"""

import pickle
import logging
import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)

# σ-multipliers for adaptive thresholds after training
_WARN_SIGMA = 2.0    # mean + 2σ  → WARNING
_CRIT_SIGMA = 3.5    # mean + 3.5σ → CRITICAL


class TransformerAutoencoder:
    """
    Input == Target autoencoder.
    Reconstruction MSE is the anomaly score; high score means anomaly.
    """

    def __init__(self, n_features: int = 19):
        self.n_features = n_features
        self.scaler     = StandardScaler()

        # Symmetric bottleneck: 19 → 14 → 8 → 5 → 8 → 14 → 19
        self.model = MLPRegressor(
            hidden_layer_sizes=(14, 8, 5, 8, 14),
            activation="relu",
            solver="adam",
            learning_rate_init=0.001,
            max_iter=800,
            tol=1e-6,
            random_state=42,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=25,
            verbose=False,
        )

        self.threshold_warn: float | None = None
        self.threshold_crit: float | None = None
        self.is_trained: bool             = False
        self._train_meta: dict            = {}

    # ── Training ──────────────────────────────────────────────────────────────
    def train(self, X: np.ndarray) -> dict:
        """
        Train on NORMAL data only.

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_features)
            Must contain ONLY normal (healthy) readings.

        Returns
        -------
        dict  Training diagnostics.
        """
        log.info(f"[AE] Training on {len(X)} normal samples …")

        X_s    = self.scaler.fit_transform(X)
        self.model.fit(X_s, X_s)          # autoencoder: output = input

        # compute reconstruction error on the training set
        X_pred = self.model.predict(X_s)
        errors = np.mean((X_s - X_pred) ** 2, axis=1)

        mu, sigma            = float(np.mean(errors)), float(np.std(errors))
        self.threshold_warn  = mu + _WARN_SIGMA * sigma
        self.threshold_crit  = mu + _CRIT_SIGMA * sigma
        self.is_trained      = True

        self._train_meta = {
            "samples":        len(X),
            "loss":           float(self.model.loss_),
            "error_mean":     round(mu,    8),
            "error_std":      round(sigma, 8),
            "threshold_warn": round(self.threshold_warn, 8),
            "threshold_crit": round(self.threshold_crit, 8),
        }

        log.info(
            f"[AE] Done — loss={self.model.loss_:.6f} "
            f"warn_thr={self.threshold_warn:.6f} "
            f"crit_thr={self.threshold_crit:.6f}"
        )
        return self._train_meta

    # ── Inference ─────────────────────────────────────────────────────────────
    def score(self, x: np.ndarray) -> float:
        """
        Reconstruction MSE for a single sample.

        Parameters
        ----------
        x : 1-D array, shape (n_features,)

        Returns
        -------
        float  Anomaly score (0 if not trained yet).
        """
        if not self.is_trained:
            return 0.0
        x_s    = self.scaler.transform(x.reshape(1, -1))
        x_pred = self.model.predict(x_s)
        return float(np.mean((x_s - x_pred) ** 2))

    def severity(self, score: float) -> int:
        """
        Map anomaly score → severity level.
        0 = Normal, 1 = Warning, 2 = Critical
        """
        if not self.is_trained or self.threshold_warn is None:
            return 0
        if score >= self.threshold_crit:
            return 2
        if score >= self.threshold_warn:
            return 1
        return 0

    def score_normalised(self, score: float) -> float:
        """Return score normalised to [0, 1] relative to the critical threshold."""
        thr = self.threshold_crit or 1e-9
        return min(1.0, score / thr)

    # ── Serialisation ─────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        return pickle.dumps({
            "scaler":         self.scaler,
            "model":          self.model,
            "threshold_warn": self.threshold_warn,
            "threshold_crit": self.threshold_crit,
            "is_trained":     self.is_trained,
            "n_features":     self.n_features,
            "train_meta":     self._train_meta,
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> "TransformerAutoencoder":
        d  = pickle.loads(data)
        ae = cls(n_features=d["n_features"])
        ae.scaler         = d["scaler"]
        ae.model          = d["model"]
        ae.threshold_warn = d["threshold_warn"]
        ae.threshold_crit = d["threshold_crit"]
        ae.is_trained     = d["is_trained"]
        ae._train_meta    = d.get("train_meta", {})
        return ae
