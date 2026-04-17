"""
ml/model_manager.py — Orchestrates the two-phase ML pipeline.

──────────────────────────────────────────────────────────────────────────────
  PHASE 1 — Unsupervised (Autoencoder)
  ─────────────────────────────────────
  • Collect readings in a rolling buffer.
  • Once ≥ MIN_TRAIN_SAMPLES normal readings exist → auto-train AE.
  • AE reconstruction MSE = anomaly_score.
  • Every anomaly is rule-labelled and saved to Firestore (anomaly_labels).
  • Retrain AE every RETRAIN_INTERVAL new readings with updated normal data.

  PHASE 2 — Supervised (Random Forest)
  ──────────────────────────────────────
  • Triggered automatically once ≥ MIN_ANOMALY_FOR_SUP anomalies are stored.
  • Trained on: labeled anomalies + balanced normal samples.
  • Outputs: fault_type, degradation score, RUL estimate.
  • Fused with AE: 40 % AE + 60 % RF.

  MODEL PERSISTENCE
  ──────────────────
  • Both models are serialised (pickle → Base64) into Firestore after training.
  • On Render restart they are reloaded, so the server picks up instantly.
──────────────────────────────────────────────────────────────────────────────
"""

import threading
import logging
import numpy as np
from collections import deque

from ml.feature_engineer import FeatureEngineer, N_FEATURES
from ml.autoencoder      import TransformerAutoencoder
from ml.supervised       import SupervisedClassifier, RUL_PRIORS

log = logging.getLogger(__name__)

# ── Hyper-parameters ──────────────────────────────────────────────────────────
MIN_TRAIN_SAMPLES   = 50    # normal readings needed to train AE
RETRAIN_INTERVAL    = 200   # re-train AE every N readings once already trained
MIN_ANOMALY_FOR_SUP = 30    # labeled anomalies needed to train supervised model
BUFFER_SIZE         = 500   # in-memory rolling buffer
LSTM_WINDOW         = 20    # window for LSTM-like degradation score


class ModelManager:

    def __init__(self, fb):
        self.fb  = fb
        self.fe  = FeatureEngineer(window=5)
        self.ae  = TransformerAutoencoder(n_features=N_FEATURES)
        self.sup = SupervisedClassifier()

        self._lock      = threading.Lock()
        self._reading_id      = 0
        self._severity        = 0
        self._since_last_train = 0
        self._training        = False

        # Rolling buffers
        self._buf_raw   = deque(maxlen=BUFFER_SIZE)   # raw dicts
        self._buf_feat  = deque(maxlen=BUFFER_SIZE)   # np arrays
        self._ae_scores = deque(maxlen=LSTM_WINDOW)   # recent AE scores

        # Load persisted models & restore buffer on startup
        self._load_models_from_firebase()
        self._restore_buffer()

    # ══════════════════════════════════════════════════════════════════════════
    #  MAIN ENTRY POINT
    # ══════════════════════════════════════════════════════════════════════════
    def process(self, reading: dict) -> dict:
        """
        Full ML pipeline for one reading.  Called by /log_reading.

        Returns a result dict that is:
          • written to Firestore  (firebase_utils)
          • returned to the ESP32 (slim subset)
        """
        # ── 1. Atomic counter ────────────────────────────────────────────
        with self._lock:
            self._reading_id += 1
            rid = self._reading_id

        # ── 2. Feature engineering ────────────────────────────────────────
        feat    = self.fe.transform(reading)
        derived = FeatureEngineer.get_derived({
            **reading, "d_winding_temp": float(feat[13])
        })

        # ── 3. Update buffers ─────────────────────────────────────────────
        self._buf_raw.append(reading)
        self._buf_feat.append(feat)
        self._since_last_train += 1

        # ── 4. Maybe trigger AE training (async, non-blocking) ────────────
        self._maybe_train_ae()

        # ── 5. AE scoring ─────────────────────────────────────────────────
        ae_score = self.ae.score(feat)
        ae_sev   = self.ae.severity(ae_score)
        self._ae_scores.append(ae_score)

        # ── 6. LSTM-like degradation score from AE history ─────────────────
        lstm_deg_score = self._compute_lstm_score()

        # ── 7. Supervised prediction ──────────────────────────────────────
        sup_result = self.sup.predict(feat)

        # ── 8. Fusion → final severity, fault_type, combined_score ────────
        fusion_mode, combined_score, fault_type, final_sev = self._fuse(
            ae_score, ae_sev, lstm_deg_score, sup_result
        )

        # ── 9. Health index ───────────────────────────────────────────────
        health_index = self._compute_health(ae_score, reading, lstm_deg_score)

        # ── 10. RUL ───────────────────────────────────────────────────────
        rul_hours = self._estimate_rul(combined_score, fault_type, lstm_deg_score)

        # ── 11. Repair urgency string ─────────────────────────────────────
        repair_urgency = _repair_urgency(final_sev, rul_hours, fault_type)

        # ── 12. Save anomaly for supervised training ───────────────────────
        if final_sev > 0 and self.ae.is_trained:
            label = (fault_type if self.sup.is_trained
                     else SupervisedClassifier.rule_based_label(reading, ae_score))
            _anomaly = {
                **reading,
                "ae_score":   ae_score,
                "lstm_score": lstm_deg_score,
                "fault_type": label,
                "severity":   final_sev,
                "reading_id": rid,
                "features":   feat.tolist(),
            }
            threading.Thread(
                target=self._async_save_anomaly,
                args=(_anomaly,), daemon=True
            ).start()

        # ── 13. Check supervised trigger ──────────────────────────────────
        threading.Thread(
            target=self.check_supervised_trigger, daemon=True
        ).start()

        # ── 14. Assemble full result ───────────────────────────────────────
        self._severity = final_sev
        return {
            # Identification
            "reading_id":       rid,
            # Raw sensors
            "oil_temp":         reading["oil_temp"],
            "winding_temp":     reading["winding_temp"],
            "current":          reading["current"],
            "vibration":        reading["vibration"],
            "oil_level":        reading["oil_level"],
            # Layer-1 derived
            **derived,
            # ML outputs
            "anomaly_score":    round(ae_score,         6),
            "lstm_deg_score":   round(lstm_deg_score,   4),
            "combined_score":   round(combined_score,   6),
            "alert_severity":   final_sev,
            "fault_type":       fault_type,
            "health_index":     round(health_index,     2),
            "rul_hours":        round(rul_hours,        1),
            "repair_urgency":   repair_urgency,
            "fusion_mode":      fusion_mode,
            "history_len":      min(len(self._ae_scores), LSTM_WINDOW),
            # Meta
            "ae_trained":       self.ae.is_trained,
            "sup_trained":      self.sup.is_trained,
            "total_readings":   rid,
        }

    # ══════════════════════════════════════════════════════════════════════════
    #  FUSION LOGIC
    # ══════════════════════════════════════════════════════════════════════════
    def _fuse(
        self,
        ae_score:      float,
        ae_sev:        int,
        lstm_deg:      float,
        sup_result:    dict,
    ) -> tuple[str, float, str, int]:
        """
        Combine AE + supervised to produce
        (fusion_mode, combined_score, fault_type, severity).
        """
        if not self.ae.is_trained:
            # ── No model yet: pure rule-based ────────────────────────────
            return "rule_only", 0.0, "NORMAL", 0

        ae_norm = self.ae.score_normalised(ae_score)   # 0-1 relative to crit thr

        if self.sup.is_trained:
            # ── Phase 2: AE 40 % + RF 60 % ───────────────────────────────
            fusion_mode    = "ae_rf_fusion"
            combined_score = 0.40 * ae_norm + 0.60 * sup_result["deg_score"]
            fault_type     = sup_result["fault_type"]
            sev = (2 if combined_score >= 0.65 else
                   1 if combined_score >= 0.35 else 0)
        else:
            # ── Phase 1: AE only, LSTM as secondary ───────────────────────
            fusion_mode    = "ae_only"
            combined_score = 0.60 * ae_norm + 0.40 * min(1.0, lstm_deg * 4)
            sev            = ae_sev
            fault_type     = "ANOMALY_DETECTED" if sev > 0 else "NORMAL"

        return fusion_mode, float(combined_score), fault_type, sev

    # ══════════════════════════════════════════════════════════════════════════
    #  LSTM-LIKE DEGRADATION SCORE
    # ══════════════════════════════════════════════════════════════════════════
    def _compute_lstm_score(self) -> float:
        """
        Approximate a sequence-aware degradation score by computing an
        exponentially-weighted moving average of the last LSTM_WINDOW AE scores,
        normalised to [0, 1].  Recent readings carry more weight.
        """
        if len(self._ae_scores) < 2:
            return 0.0
        scores  = np.array(self._ae_scores, dtype=np.float64)
        weights = np.exp(np.linspace(-1.0, 0.0, len(scores)))
        weights /= weights.sum()
        ewma    = float(np.dot(weights, scores))
        thr     = self.ae.threshold_crit or 1e-9
        return min(1.0, ewma / thr)

    # ══════════════════════════════════════════════════════════════════════════
    #  HEALTH INDEX
    # ══════════════════════════════════════════════════════════════════════════
    def _compute_health(
        self, ae_score: float, r: dict, lstm_deg: float
    ) -> float:
        """
        100 % = perfectly healthy, 0 % = failed.
        Weighted blend of AE anomaly score + sensor rule penalties.
        """
        if not self.ae.is_trained:
            return _rule_health(r)

        ae_component = self.ae.score_normalised(ae_score)   # 0–1

        ot  = float(r.get("oil_temp",      25))
        wt  = float(r.get("winding_temp",  25))
        I   = float(r.get("current",        0))
        vib = float(r.get("vibration",      0))
        oil = float(r.get("oil_level",    100))

        thermal_pen  = min(1.0, max(0.0, (wt  - 65.0) / 25.0))
        vibration_pen = min(1.0, max(0.0, (vib - 0.5)  /  2.5))
        oil_pen      = min(1.0, max(0.0, (40.0 - oil)  / 40.0))
        overload_pen = min(1.0, max(0.0, (I   - 15.0)  / 10.0))

        sensor_pen   = (thermal_pen   * 0.35 + vibration_pen * 0.20
                        + oil_pen     * 0.25 + overload_pen  * 0.20)
        combined_pen = 0.50 * ae_component + 0.30 * sensor_pen + 0.20 * lstm_deg

        return max(0.0, round(100.0 * (1.0 - combined_pen), 2))

    # ══════════════════════════════════════════════════════════════════════════
    #  RUL ESTIMATION
    # ══════════════════════════════════════════════════════════════════════════
    def _estimate_rul(
        self, combined_score: float, fault_type: str, lstm_deg: float
    ) -> float:
        if not self.ae.is_trained:
            return -1.0      # unknown until AE is trained

        if combined_score < 0.10 and lstm_deg < 0.10:
            return 200.0     # healthy

        rul_base   = RUL_PRIORS.get(fault_type, 100.0)
        deg_factor = max(0.0, 1.0 - combined_score)
        return max(0.5, min(200.0, round(rul_base * deg_factor, 1)))

    # ══════════════════════════════════════════════════════════════════════════
    #  AE TRAINING  (async)
    # ══════════════════════════════════════════════════════════════════════════
    def _maybe_train_ae(self):
        n = len(self._buf_raw)
        should = (
            not self._training
            and (
                (not self.ae.is_trained and n >= MIN_TRAIN_SAMPLES)
                or (self.ae.is_trained and self._since_last_train >= RETRAIN_INTERVAL)
            )
        )
        if should:
            self._training         = True
            self._since_last_train = 0
            threading.Thread(target=self._train_ae_async, daemon=True).start()

    def _train_ae_async(self):
        try:
            log.info("[AE] Async training started …")
            X = np.array(list(self._buf_feat), dtype=np.float32)

            # Only use samples that look normal (below warn threshold if available)
            if self.ae.is_trained and self.ae.threshold_warn:
                scores    = np.array([self.ae.score(x) for x in X])
                mask      = scores < self.ae.threshold_warn
                X_normal  = X[mask]
                if len(X_normal) < 30:
                    X_normal = X   # not enough normal data yet, use all
            else:
                X_normal = X

            stats = self.ae.train(X_normal)

            # Persist to Firestore (survives Render restarts)
            threading.Thread(
                target=self.fb.save_model_bytes,
                args=("autoencoder", self.ae.to_bytes(), stats),
                daemon=True,
            ).start()
        except Exception as e:
            log.error(f"[AE] Training error: {e}", exc_info=True)
        finally:
            self._training = False

    # ══════════════════════════════════════════════════════════════════════════
    #  SUPERVISED TRAINING  (async)
    # ══════════════════════════════════════════════════════════════════════════
    def check_supervised_trigger(self):
        """Called after every reading.  Trains supervised model when ready."""
        try:
            count = self.fb.get_anomaly_count()
            if count >= MIN_ANOMALY_FOR_SUP and not self._training:
                threading.Thread(
                    target=self._train_supervised_async, daemon=True
                ).start()
        except Exception as e:
            log.error(f"[SUP] check_supervised_trigger: {e}")

    def _train_supervised_async(self):
        try:
            log.info("[SUP] Loading labeled anomalies from Firestore …")
            anomalies = self.fb.get_anomaly_labels(limit=500)
            if not anomalies:
                return

            rows, labels = [], []
            for a in anomalies:
                feats = a.get("features")
                label = a.get("fault_type", "ANOMALY_DETECTED")
                if feats and len(feats) == N_FEATURES:
                    rows.append(feats)
                    labels.append(label)

            if len(rows) < 20:
                log.info("[SUP] Not enough valid feature rows yet")
                return

            # ── Balance with normal readings (up to 2× anomaly count) ────
            normal_X = []
            for feat in list(self._buf_feat)[-200:]:
                if self.ae.is_trained and self.ae.score(feat) < (self.ae.threshold_warn or 1e9):
                    normal_X.append(feat.tolist())

            n_add = min(len(normal_X), len(rows) * 2)
            for fl in normal_X[:n_add]:
                rows.append(fl)
                labels.append("NORMAL")

            X     = np.array(rows, dtype=np.float32)
            stats = self.sup.train(X, labels)

            # Persist
            threading.Thread(
                target=self.fb.save_model_bytes,
                args=("supervised", self.sup.to_bytes(), stats),
                daemon=True,
            ).start()
        except Exception as e:
            log.error(f"[SUP] Training error: {e}", exc_info=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  PERSISTENCE / BUFFER RESTORE
    # ══════════════════════════════════════════════════════════════════════════
    def _load_models_from_firebase(self):
        """Reload trained models from Firestore on server startup."""
        try:
            b = self.fb.load_model_bytes("autoencoder")
            if b:
                self.ae = TransformerAutoencoder.from_bytes(b)
                log.info(f"[BOOT] AE loaded (trained={self.ae.is_trained})")
        except Exception as e:
            log.warning(f"[BOOT] Could not load AE: {e}")

        try:
            b = self.fb.load_model_bytes("supervised")
            if b:
                self.sup = SupervisedClassifier.from_bytes(b)
                log.info(f"[BOOT] Supervised loaded (trained={self.sup.is_trained})")
        except Exception as e:
            log.warning(f"[BOOT] Could not load Supervised: {e}")

    def _restore_buffer(self):
        """Repopulate in-memory feature buffer from recent Firestore predictions."""
        try:
            rows = self.fb.get_recent_readings(limit=200)
            if not rows:
                return
            log.info(f"[BOOT] Restoring buffer from {len(rows)} recent readings …")
            fe_tmp = FeatureEngineer(window=5)
            keys   = ["oil_temp", "winding_temp", "current", "vibration", "oil_level"]
            for r in rows:
                if all(k in r for k in keys):
                    raw  = {k: r[k] for k in keys}
                    feat = fe_tmp.transform(raw)
                    self._buf_raw.append(raw)
                    self._buf_feat.append(feat)
                    self._ae_scores.append(float(r.get("anomaly_score", 0.0)))
            self._reading_id = len(self._buf_raw)
            log.info(f"[BOOT] Buffer restored: {self._reading_id} readings")
        except Exception as e:
            log.warning(f"[BOOT] Buffer restore failed: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  PUBLIC MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════
    def reload_models(self):
        self._load_models_from_firebase()

    def reset(self):
        with self._lock:
            self.ae  = TransformerAutoencoder(n_features=N_FEATURES)
            self.sup = SupervisedClassifier()
            self.fe.reset()
            self._buf_raw.clear()
            self._buf_feat.clear()
            self._ae_scores.clear()
            self._reading_id       = 0
            self._severity         = 0
            self._since_last_train = 0
        log.info("[MGR] Full reset complete")

    def clear_severity(self):
        self._severity = 0

    def get_status(self) -> dict:
        n          = len(self._buf_raw)
        until_train = max(0, MIN_TRAIN_SAMPLES - n) if not self.ae.is_trained else 0
        return {
            "total_readings":          self._reading_id,
            "buffer_size":             n,
            "model_ready":             self.ae.is_trained,
            "ae_model_ready":          self.ae.is_trained,
            "supervised_model_ready":  self.sup.is_trained,
            "fusion_mode":             (
                "ae_rf_fusion" if self.sup.is_trained else
                "ae_only"      if self.ae.is_trained  else
                "rule_only"
            ),
            "ae_threshold_warn":       self.ae.threshold_warn,
            "ae_threshold_crit":       self.ae.threshold_crit,
            "min_train_samples":       MIN_TRAIN_SAMPLES,
            "samples_until_train":     until_train,
            "min_anomaly_for_sup":     MIN_ANOMALY_FOR_SUP,
        }

    # ── Helper: async anomaly save ────────────────────────────────────────────
    def _async_save_anomaly(self, doc: dict):
        try:
            self.fb.save_anomaly_label(doc)
        except Exception as e:
            log.error(f"[MGR] _async_save_anomaly: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  MODULE-LEVEL HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _rule_health(r: dict) -> float:
    """Fallback rule-based health before AE is trained."""
    penalties = 0.0
    wt  = float(r.get("winding_temp", 25))
    ot  = float(r.get("oil_temp",     25))
    vib = float(r.get("vibration",     0))
    oil = float(r.get("oil_level",   100))
    I   = float(r.get("current",       0))
    if wt  > 65:  penalties += (wt  - 65)  / 30
    if ot  > 55:  penalties += (ot  - 55)  / 30
    if vib > 0.5: penalties += (vib - 0.5) / 2.5
    if oil < 60:  penalties += (60  - oil) / 100
    if I   > 15:  penalties += (I   - 15)  / 10
    return max(0.0, round(100.0 * (1.0 - min(1.0, penalties)), 2))


def _repair_urgency(sev: int, rul: float, fault_type: str) -> str:
    if sev == 0:
        return "NONE — Normal Operation"
    if sev == 2 or rul < 4:
        return "IMMEDIATE — Shut down within 4 hours"
    if rul < 24:
        return f"URGENT — Maintenance within 24h ({fault_type})"
    if rul < 72:
        return f"SCHEDULED — Plan maintenance within 3 days ({fault_type})"
    return f"MONITOR — Degradation detected ({fault_type})"
