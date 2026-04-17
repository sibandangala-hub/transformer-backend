"""
Transformer PM Backend — Render + Firebase
Phase 1 : Unsupervised  (Autoencoder)  — anomalies auto-labelled & stored
Phase 2 : Supervised    (Random Forest) — triggered when enough labels exist
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import logging, os, traceback

from firebase_utils import FirebaseUtils
from ml.model_manager import ModelManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ── Global singletons ─────────────────────────────────────────────────────────
fb  = FirebaseUtils()          # Firestore helper
mgr = ModelManager(fb)         # ML pipeline orchestrator


# ─────────────────────────────────────────────────────────────────────────────
#  POST /log_reading          ← ESP32 sends sensor data here every 2 s
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/log_reading", methods=["POST"])
def log_reading():
    try:
        data     = request.get_json(force=True) or {}
        required = ["oil_temp", "winding_temp", "current", "vibration", "oil_level"]
        missing  = [k for k in required if k not in data]
        if missing:
            return jsonify({"error": f"Missing fields: {missing}"}), 400

        reading = {k: float(data[k]) for k in required}

        # ── Run full ML pipeline ──────────────────────────────────────────
        result = mgr.process(reading)

        # ── Persist to Firestore (async inside helpers) ───────────────────
        fb.save_prediction(result)
        fb.update_status(result)
        if result.get("alert_severity", 0) > 0:
            fb.log_fault(result)

        # ── Slim response back to ESP32 ───────────────────────────────────
        return jsonify({
            "reading_id":     result["reading_id"],
            "anomaly_score":  result["anomaly_score"],
            "alert_severity": result["alert_severity"],
            "health_index":   result["health_index"],
            "fault_type":     result["fault_type"],
        }), 200

    except Exception:
        log.error(traceback.format_exc())
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/log              ← frontend "Test Alarm" button / manual inject
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/log", methods=["POST"])
def api_log():
    return log_reading()


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/status
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(mgr.get_status()), 200


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/acknowledge
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/acknowledge", methods=["POST"])
def api_acknowledge():
    fb.acknowledge()
    mgr.clear_severity()
    return jsonify({"ok": True}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/clear_faults
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/clear_faults", methods=["POST"])
def api_clear_faults():
    fb.clear_faults()
    return jsonify({"ok": True}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/reset_readings
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/reset_readings", methods=["POST"])
def api_reset():
    fb.reset_all()
    mgr.reset()
    return jsonify({"ok": True}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/reload
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/reload", methods=["POST"])
def api_reload():
    mgr.reload_models()
    s = mgr.get_status()
    return jsonify({
        "ae_model_ready":   s["ae_model_ready"],
        "lstm_model_ready": s["supervised_model_ready"],
        "rul_model_ready":  s["supervised_model_ready"],
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/label_anomaly    ← correct an anomaly label (improves supervised)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/label_anomaly", methods=["POST"])
def api_label():
    data       = request.get_json(force=True) or {}
    anomaly_id = data.get("anomaly_id")
    label      = data.get("label")
    if not anomaly_id or not label:
        return jsonify({"error": "Need anomaly_id and label"}), 400
    fb.update_anomaly_label(anomaly_id, label)
    mgr.check_supervised_trigger()   # maybe enough data now
    return jsonify({"ok": True}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/anomaly_labels    ← list all saved anomalies (for labelling UI)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/anomaly_labels", methods=["GET"])
def api_anomaly_labels():
    labels = fb.get_anomaly_labels(limit=100)
    return jsonify(labels), 200


# ─────────────────────────────────────────────────────────────────────────────
#  Health / root
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
@app.route("/",       methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "transformer-pm"}), 200


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
