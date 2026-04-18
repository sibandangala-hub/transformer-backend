# ml/drift_detector.py
import numpy as np
import pandas as pd
import json
from scipy import stats
import joblib
import firebase_admin
from firebase_admin import firestore

class DriftDetector:
    """
    Uses Page-Hinkley test for sequential drift detection.
    Much more sensitive than simple threshold comparison.
    Runs on a sliding window of recent readings vs training baseline.
    """

    def __init__(self, threshold=50.0, alpha=0.01):
        self.threshold = threshold   # sensitivity — lower = more sensitive
        self.alpha     = alpha
        self.reset()

    def reset(self):
        self.cumsum   = 0.0
        self.min_val  = float('inf')
        self.n        = 0
        self.mean_est = None

    def update(self, value) -> bool:
        """Returns True if drift detected."""
        self.n += 1
        if self.mean_est is None:
            self.mean_est = value
        self.mean_est += (value - self.mean_est) / self.n
        self.cumsum  += (value - self.mean_est - self.alpha)
        self.min_val  = min(self.min_val, self.cumsum)
        return (self.cumsum - self.min_val) > self.threshold


def check_feature_drift(baseline_stats: dict, recent_df: pd.DataFrame) -> dict:
    """
    KS test comparing recent distribution vs training baseline.
    Returns per-feature drift p-values and a global drift flag.
    """
    FEATURES = ['winding_temp', 'current', 'vibration', 'oil_level']
    results  = {}
    any_drift = False

    for feat in FEATURES:
        recent_vals = recent_df[feat].dropna().values
        # Reconstruct approximate baseline distribution from saved mean/std
        baseline_samples = np.random.normal(
            baseline_stats[feat]['mean'],
            baseline_stats[feat]['std'],
            size=1000
        )
        stat, pvalue = stats.ks_2samp(baseline_samples, recent_vals)
        drifted = pvalue < 0.05   # 95% confidence
        results[feat] = {
            'ks_statistic': round(float(stat), 4),
            'p_value':      round(float(pvalue), 4),
            'drifted':      drifted,
            'recent_mean':  round(float(recent_vals.mean()), 3),
            'baseline_mean': round(baseline_stats[feat]['mean'], 3)
        }
        if drifted:
            any_drift = True
            print(f"[DRIFT] {feat}: p={pvalue:.4f} — DRIFT DETECTED")

    results['global_drift'] = any_drift
    return results