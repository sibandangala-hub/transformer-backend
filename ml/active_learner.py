# ml/active_learner.py
import numpy as np
import joblib
import json
from google.cloud import firestore

class ActiveLearner:
    """
    Uncertainty sampling: when the model is not confident,
    flag the reading for human review instead of guessing.
    
    Three uncertainty strategies:
    - Least confidence:  1 - P(most likely class)
    - Margin sampling:   P(1st) - P(2nd)
    - Entropy:           -sum(p * log(p))  ← most informative
    """

    def __init__(self, rf_model, gb_model, entropy_threshold=0.6):
        self.rf  = rf_model
        self.gb  = gb_model
        self.entropy_threshold = entropy_threshold

    def entropy(self, proba: np.ndarray) -> float:
        proba = np.clip(proba, 1e-10, 1.0)
        return float(-np.sum(proba * np.log(proba)))

    def should_query(self, X_scaled: np.ndarray) -> dict:
        rf_proba  = self.rf.predict_proba(X_scaled)[0]
        gb_proba  = self.gb.predict_proba(X_scaled)[0]
        avg_proba = (rf_proba + gb_proba) / 2.0

        ent  = self.entropy(avg_proba)
        top1 = float(avg_proba.max())
        top2 = float(np.sort(avg_proba)[-2]) if len(avg_proba) > 1 else 0.0

        # Models disagree with each other — another reason to flag
        rf_class = int(np.argmax(rf_proba))
        gb_class = int(np.argmax(gb_proba))
        model_disagreement = rf_class != gb_class

        needs_label = (ent > self.entropy_threshold) or model_disagreement

        return {
            'needs_label':        needs_label,
            'entropy':            round(ent, 4),
            'top1_confidence':    round(top1, 4),
            'margin':             round(top1 - top2, 4),
            'model_disagreement': model_disagreement,
            'rf_class':           rf_class,
            'gb_class':           gb_class
        }

    def flag_for_review(self, db, reading_id: str, uncertainty_info: dict):
        """Write to Firebase so your dashboard shows pending reviews."""
        db.collection('review_queue').add({
            'reading_id':   reading_id,
            'entropy':      uncertainty_info['entropy'],
            'margin':       uncertainty_info['margin'],
            'disagreement': uncertainty_info['model_disagreement'],
            'status':       'pending',
            'created_at':   firestore.SERVER_TIMESTAMP
        })
        print(f"[ActiveLearner] Flagged {reading_id} for review (entropy={uncertainty_info['entropy']:.3f})")