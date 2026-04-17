"""
firebase_utils.py — All Firestore read / write helpers.

On Render, set the env var:
    FIREBASE_SERVICE_ACCOUNT_JSON = <paste the full JSON from Firebase Console>

For local dev you can also set:
    FIREBASE_SA_PATH = path/to/serviceAccountKey.json
"""

import os, json, base64, logging
import firebase_admin
from firebase_admin import credentials, firestore

log = logging.getLogger(__name__)


class FirebaseUtils:
    def __init__(self):
        self.db = self._init()
        log.info("[Firebase] Firestore connected")

    # ── Initialise ────────────────────────────────────────────────────────────
    def _init(self):
        if firebase_admin._apps:
            return firestore.client()

        sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
        if sa_json:
            cred = credentials.Certificate(json.loads(sa_json))
        else:
            sa_path = os.environ.get("FIREBASE_SA_PATH", "serviceAccountKey.json")
            cred = credentials.Certificate(sa_path)

        firebase_admin.initialize_app(cred)
        return firestore.client()

    @staticmethod
    def _ts():
        return firestore.SERVER_TIMESTAMP

    # ── Predictions ───────────────────────────────────────────────────────────
    def save_prediction(self, result: dict):
        """
        Write one prediction row to the 'predictions' collection.
        The frontend polls this collection (orderBy timestamp, limit 60).
        """
        try:
            doc = {k: v for k, v in result.items() if not isinstance(v, list)}
            doc["timestamp"] = self._ts()
            self.db.collection("predictions").add(doc)
        except Exception as e:
            log.error(f"[Firebase] save_prediction: {e}")

    # ── status/latest ─────────────────────────────────────────────────────────
    def update_status(self, result: dict):
        """
        Overwrite the single 'status/latest' document.
        The frontend uses onSnapshot on this document for live updates.
        """
        try:
            doc = {k: v for k, v in result.items() if not isinstance(v, list)}
            doc["timestamp"] = self._ts()
            self.db.collection("status").document("latest").set(doc)
        except Exception as e:
            log.error(f"[Firebase] update_status: {e}")

    # ── Fault log ─────────────────────────────────────────────────────────────
    def log_fault(self, result: dict):
        """
        Append a fault event.  cleared=False initially so the frontend shows it.
        """
        try:
            doc = {k: v for k, v in result.items() if not isinstance(v, list)}
            doc["timestamp"] = self._ts()
            doc["cleared"]   = False
            self.db.collection("fault_log").add(doc)
        except Exception as e:
            log.error(f"[Firebase] log_fault: {e}")

    def acknowledge(self):
        try:
            (self.db.collection("status").document("latest")
             .update({"acknowledged": True, "alert_severity": 0}))
        except Exception as e:
            log.error(f"[Firebase] acknowledge: {e}")

    def clear_faults(self):
        try:
            docs = (self.db.collection("fault_log")
                    .where("cleared", "==", False).get())
            for d in docs:
                d.reference.update({"cleared": True})
        except Exception as e:
            log.error(f"[Firebase] clear_faults: {e}")

    def reset_all(self):
        try:
            for col in ["predictions", "fault_log", "anomaly_labels", "readings"]:
                docs = self.db.collection(col).limit(500).get()
                for d in docs:
                    d.reference.delete()
            self.db.collection("status").document("latest").delete()
            self.db.collection("ml_models").document("autoencoder").delete()
            self.db.collection("ml_models").document("supervised").delete()
        except Exception as e:
            log.error(f"[Firebase] reset_all: {e}")

    # ── Anomaly labels (training data for Phase 2) ────────────────────────────
    def save_anomaly_label(self, anomaly: dict) -> str | None:
        """
        Save an anomaly with its rule-based initial label.
        Returns the Firestore document ID so the user can later confirm / correct it.
        """
        try:
            doc = {k: v for k, v in anomaly.items() if not isinstance(v, list)}
            doc["timestamp"] = self._ts()
            doc["confirmed"] = False   # True once a human verifies the label
            _, ref = self.db.collection("anomaly_labels").add(doc)
            return ref.id
        except Exception as e:
            log.error(f"[Firebase] save_anomaly_label: {e}")
            return None

    def update_anomaly_label(self, anomaly_id: str, label: str):
        try:
            (self.db.collection("anomaly_labels").document(anomaly_id)
             .update({"fault_type": label, "confirmed": True}))
        except Exception as e:
            log.error(f"[Firebase] update_anomaly_label: {e}")

    def get_anomaly_labels(self, limit: int = 200, confirmed_only: bool = False):
        try:
            q = self.db.collection("anomaly_labels")
            if confirmed_only:
                q = q.where("confirmed", "==", True)
            docs = (q.order_by("timestamp",
                               direction=firestore.Query.DESCENDING)
                    .limit(limit).get())
            result = []
            for d in docs:
                row      = d.to_dict()
                row["id"] = d.id
                ts = row.get("timestamp")
                if hasattr(ts, "isoformat"):
                    row["timestamp"] = ts.isoformat()
                result.append(row)
            return result
        except Exception as e:
            log.error(f"[Firebase] get_anomaly_labels: {e}")
            return []

    def get_anomaly_count(self) -> int:
        try:
            return len(self.db.collection("anomaly_labels").get())
        except Exception:
            return 0

    # ── ML model persistence (handles Render restarts) ────────────────────────
    def save_model_bytes(self, model_name: str, data: bytes, meta: dict = None):
        """
        Serialise a trained model as Base64 and store it in Firestore.
        This survives Render free-tier restarts which erase the filesystem.
        """
        try:
            b64 = base64.b64encode(data).decode("utf-8")
            self.db.collection("ml_models").document(model_name).set({
                "model_b64": b64,
                "updated":   self._ts(),
                "meta":      meta or {},
            })
            log.info(f"[Firebase] Saved model '{model_name}' ({len(data)//1024} KB)")
        except Exception as e:
            log.error(f"[Firebase] save_model_bytes: {e}")

    def load_model_bytes(self, model_name: str) -> bytes | None:
        try:
            doc = self.db.collection("ml_models").document(model_name).get()
            if not doc.exists:
                return None
            b64 = doc.to_dict().get("model_b64", "")
            return base64.b64decode(b64) if b64 else None
        except Exception as e:
            log.error(f"[Firebase] load_model_bytes: {e}")
            return None

    # ── Buffer restore on startup ─────────────────────────────────────────────
    def get_recent_readings(self, limit: int = 200) -> list:
        """Fetch recent predictions to rebuild in-memory buffer after restart."""
        try:
            docs = (self.db.collection("predictions")
                    .order_by("timestamp", direction=firestore.Query.DESCENDING)
                    .limit(limit).get())
            rows = [d.to_dict() for d in docs]
            rows.reverse()   # oldest first
            return rows
        except Exception as e:
            log.error(f"[Firebase] get_recent_readings: {e}")
            return []
