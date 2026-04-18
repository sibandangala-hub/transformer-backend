# ml/predict.py  — called by Express via child_process or as a Flask microservice
import sys
import json
import numpy as np
import joblib
import tensorflow as tf

def predict(reading: dict, history: list) -> dict:
    """
    reading: latest sensor dict {winding_temp, current, vibration, oil_level}
    history: list of last 30 readings in order (for LSTM + rolling features)
    """
    with open('model_metadata_phase2.json') as f:
        meta = json.load(f)

    scaler = joblib.load('scaler_phase2.pkl')
    rf     = joblib.load('random_forest_phase2.pkl')
    gb     = joblib.load('gradient_boosting_phase2.pkl')
    lstm   = tf.keras.models.load_model('lstm_phase2.keras')
    iso    = joblib.load('isolation_forest_phase1.pkl')
    ae     = tf.keras.models.load_model('autoencoder_phase1.keras')
    with open('thresholds_phase1.json') as f:
        thresholds = json.load(f)

    # Build feature vector with rolling stats from history
    hist = history[-10:] if len(history) >= 10 else history
    temps    = [h['winding_temp'] for h in hist]
    currents = [h['current'] for h in hist]
    vibs     = [h['vibration'] for h in hist]

    features = [
        reading['winding_temp'],
        reading['current'],
        reading['vibration'],
        reading['oil_level'],
        np.mean(temps)  if temps else reading['winding_temp'],
        np.std(temps)   if len(temps) > 1 else 0.0,
        np.mean(currents) if currents else reading['current'],
        np.max(vibs)    if vibs else reading['vibration'],
        reading['winding_temp'] - (temps[-1] if temps else reading['winding_temp']),
        reading['current']      - (currents[-1] if currents else reading['current']),
    ]

    X = np.array([features])
    X_scaled = scaler.transform(X)

    # Isolation Forest anomaly check
    iso_score = float(iso.decision_function(X_scaled)[0])
    ae_recon  = float(np.mean(np.square(X_scaled - ae.predict(X_scaled, verbose=0))))
    is_anomaly_iso = iso.predict(X_scaled)[0] == -1
    is_anomaly_ae  = ae_recon > thresholds['ae_reconstruction_threshold']

    # RF + GB classification (only meaningful if we have supervised model)
    rf_proba = rf.predict_proba(X_scaled)[0]
    gb_proba = gb.predict_proba(X_scaled)[0]

    # Ensemble: average RF and GB probabilities
    ensemble_proba = (rf_proba + gb_proba) / 2.0
    predicted_class = int(np.argmax(ensemble_proba))
    confidence = float(ensemble_proba[predicted_class])

    # LSTM sequence prediction (if enough history)
    lstm_class, lstm_confidence = None, None
    if len(history) >= meta['seq_len']:
        hist_seq = history[-meta['seq_len']:]
        seq_features = []
        for i, h in enumerate(hist_seq):
            sub = hist_seq[max(0,i-10):i]
            t_hist = [s['winding_temp'] for s in sub]
            c_hist = [s['current'] for s in sub]
            v_hist = [s['vibration'] for s in sub]
            seq_features.append([
                h['winding_temp'], h['current'], h['vibration'], h['oil_level'],
                np.mean(t_hist) if t_hist else h['winding_temp'],
                np.std(t_hist)  if len(t_hist) > 1 else 0.0,
                np.mean(c_hist) if c_hist else h['current'],
                np.max(v_hist)  if v_hist else h['vibration'],
                h['winding_temp'] - (t_hist[-1] if t_hist else h['winding_temp']),
                h['current']      - (c_hist[-1] if c_hist else h['current']),
            ])
        X_seq = np.array([scaler.transform(seq_features)])
        lstm_proba = lstm.predict(X_seq, verbose=0)[0]
        lstm_class = int(np.argmax(lstm_proba))
        lstm_confidence = float(lstm_proba[lstm_class])

    # Final severity determination
    label_map = meta['class_labels']
    severity = 'normal'
    if predicted_class != 0 and confidence > 0.7:
        severity = 'critical' if confidence > 0.9 else 'medium'
    elif is_anomaly_iso and is_anomaly_ae:
        severity = 'medium'
    elif is_anomaly_iso or is_anomaly_ae:
        severity = 'low'

    return {
        'predicted_class':      predicted_class,
        'predicted_label':      label_map[str(predicted_class)],
        'confidence':           round(confidence, 4),
        'severity':             severity,
        'iso_anomaly':          bool(is_anomaly_iso),
        'ae_anomaly':           bool(is_anomaly_ae),
        'ae_reconstruction_error': round(ae_recon, 6),
        'lstm_class':           lstm_class,
        'lstm_confidence':      round(lstm_confidence, 4) if lstm_confidence else None,
    }