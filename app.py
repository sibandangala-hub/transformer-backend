import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timezone

app = Flask(__name__)

# ── Firestore init ─────────────────────────────────────────────
cred = credentials.Certificate("/etc/secrets/firebase_credentials.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

API_KEY = os.environ.get("API_KEY", "your_secret_key_123")

# ── Auth check ──────────────────────────────────────────────────
def check_api_key():
    return request.headers.get("x-api-key") == API_KEY

# ── POST /api/data  (ESP32 posts here) ─────────────────────────
@app.route("/api/data", methods=["POST"])
def receive_data():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "No JSON body"}), 400

    required = ["winding_temp", "current", "vibration", "oil_level"]
    for field in required:
        if field not in body:
            return jsonify({"error": f"Missing field: {field}"}), 400

    record = {
        "winding_temp": float(body["winding_temp"]),
        "current":      float(body["current"]),
        "vibration":    float(body["vibration"]),
        "oil_level":    float(body["oil_level"]),
        "label":        "normal",                        # Phase 1 — all normal
        "timestamp":    datetime.now(timezone.utc)       # server-side UTC time
    }

    db.collection("sensor_readings").add(record)

    return jsonify({"status": "ok"}), 200

# ── GET /api/data  (sanity check / dashboard later) ────────────
@app.route("/api/data", methods=["GET"])
def get_data():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    docs = db.collection("sensor_readings")\
             .order_by("timestamp", direction=firestore.Query.DESCENDING)\
             .limit(50)\
             .stream()

    results = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        d["timestamp"] = d["timestamp"].isoformat()
        results.append(d)

    return jsonify(results), 200

# ── Health check ────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "Transformer PM backend running"}), 200

if __name__ == "__main__":
    app.run(debug=False)
