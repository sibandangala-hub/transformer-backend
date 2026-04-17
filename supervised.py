"""
ml/supervised.py — Phase 2: Supervised fault classifier.

Once the Autoencoder has detected and stored ≥ MIN_ANOMALY_FOR_SUP anomalies,
this Random Forest classifier is trained on:
    • labeled anomaly readings  (fault classes)
    • recent normal readings    (NORMAL class, balanced 2:1)

Outputs
-------
fault_type   : one of FAULT_TYPES
deg_score    : 1 − P(NORMAL) in [0, 1] — used as the "LSTM" score in fusion
rul_hours    : estimated remaining useful life (h), from per-fault priors + deg_score
"""

import pickle
import logging
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder

log = logging.getLogger(__name__)

# ── Fault taxonomy ────────────────────────────────────────────────────────────
FAULT_TYPES = [
    "NORMAL",
    "OVERHEATING",
    "WINDING_FAULT",
    "OIL_DEGRADATION",
    "VIBRATION_FAULT",
    "OVERLOAD",
    "COMPOUND_FAULT",
]

# Conservative RUL priors (hours) — scaled by degradation score at inference
RUL_PRIORS = {
    "NORMAL":           200.0,
    "OVERHEATING":       48.0,
    "WINDING_FAULT":     24.0,
    "OIL_DEGRADATION":   72.0,
    "VIBRATION_FAULT":   36.0,
    "OVERLOAD":          12.0,
    "COMPOUND_FAULT":     6.0,
}


class SupervisedClassifier:
    """
    Random Forest fault-type classifier.
    Trained on labeled anomalies stored by Phase 1.
    """

    def __init__(self):
        self.clf         = RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=2,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        self.le          = LabelEncoder()
        self.is_trained  = False
        self.classes_: list[str] = []
        self.train_meta: dict    = {}

    # ── Training ──────────────────────────────────────────────────────────────
    def train(self, X: np.ndarray, y_labels: list[str]) -> dict:
        """
        Parameters
        ----------
        X        : (n_samples, n_features)
        y_labels : list of fault-type strings
        """
        log.info(f"[SUP] Training on {len(X)} samples …")

        y = self.le.fit_transform(y_labels)
        self.classes_ = list(self.le.classes_)
        self.clf.fit(X, y)
        self.is_trained = True

        train_acc      = float(self.clf.score(X, y))
        n_normal       = sum(1 for l in y_labels if l == "NORMAL")
        n_anomaly      = len(y_labels) - n_normal

        self.train_meta = {
            "samples":   len(X),
            "train_acc": round(train_acc, 4),
            "classes":   self.classes_,
            "n_normal":  n_normal,
            "n_anomaly": n_anomaly,
        }
        log.info(
            f"[SUP] Done — acc={train_acc:.3f} "
            f"classes={self.classes_} "
            f"normal={n_normal} anomaly={n_anomaly}"
        )
        return self.train_meta

    # ── Inference ─────────────────────────────────────────────────────────────
    def predict(self, x: np.ndarray) -> dict:
        """
        Parameters
        ----------
        x : 1-D feature vector, shape (n_features,)

        Returns
        -------
        dict with keys: fault_type, fault_proba, deg_score, rul_hours
        """
        if not self.is_trained:
            return {
                "fault_type":  "NORMAL",
                "fault_proba": 1.0,
                "deg_score":   0.0,
                "rul_hours":   200.0,
            }

        proba     = self.clf.predict_proba(x.reshape(1, -1))[0]
        class_idx = int(np.argmax(proba))
        fault_type  = self.classes_[class_idx]
        fault_proba = float(proba[class_idx])

        # Degradation score = 1 − P(NORMAL)
        try:
            normal_idx = self.classes_.index("NORMAL")
            deg_score  = float(1.0 - proba[normal_idx])
        except ValueError:
            deg_score  = float(1.0 - fault_proba) if fault_type == "NORMAL" else float(fault_proba)

        # RUL: start from prior, scale down by degradation
        rul_base  = RUL_PRIORS.get(fault_type, 100.0)
        rul_hours = max(0.5, rul_base * (1.0 - deg_score * 0.5))

        return {
            "fault_type":  fault_type,
            "fault_proba": round(fault_proba, 4),
            "deg_score":   round(deg_score,   4),
            "rul_hours":   round(rul_hours,   1),
        }

    # ── Rule-based initial labelling (used when sup model isn't ready yet) ────
    @staticmethod
    def rule_based_label(reading: dict, ae_score: float) -> str:
        """
        Assign an initial fault label using sensor thresholds.
        This auto-labels every AE anomaly so Phase 2 training can start
        without manual intervention.  Humans can later confirm / correct via
        POST /api/label_anomaly.
        """
        ot  = float(reading.get("oil_temp",      25))
        wt  = float(reading.get("winding_temp",  25))
        I   = float(reading.get("current",        0))
        vib = float(reading.get("vibration",      0))
        oil = float(reading.get("oil_level",    100))

        # Priority: most serious conditions first
        if wt > 75 and ot > 60:
            return "COMPOUND_FAULT"
        if wt > 75:
            return "WINDING_FAULT"
        if ot > 65:
            return "OVERHEATING"
        if wt > 68:
            return "OVERHEATING"
        if vib > 2.0:
            return "VIBRATION_FAULT"
        if I > 18.0:
            return "OVERLOAD"
        if oil < 30.0:
            return "OIL_DEGRADATION"
        if oil < 50.0 and ot > 55.0:
            return "OIL_DEGRADATION"
        # AE detected anomaly but no specific threshold crossed → thermal catch-all
        return "OVERHEATING"

    # ── Serialisation ─────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        return pickle.dumps({
            "clf":        self.clf,
            "le":         self.le,
            "is_trained": self.is_trained,
            "classes_":   self.classes_,
            "train_meta": self.train_meta,
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> "SupervisedClassifier":
        d  = pickle.loads(data)
        sc = cls()
        sc.clf        = d["clf"]
        sc.le         = d["le"]
        sc.is_trained = d["is_trained"]
        sc.classes_   = d["classes_"]
        sc.train_meta = d.get("train_meta", {})
        return sc
