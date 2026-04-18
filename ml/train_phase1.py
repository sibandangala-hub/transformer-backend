import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
import tensorflow as tf
from tensorflow import keras
import joblib
import json
import matplotlib.pyplot as plt

# ─────────────────────────────────────────────
#  LOAD DATA
# ─────────────────────────────────────────────
df = pd.read_csv('readings.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)

# Drop rows with missing sensor values
df = df.dropna(subset=['winding_temp', 'current', 'vibration', 'oil_level'])

print(f"Loaded {len(df)} readings spanning {df['timestamp'].min()} → {df['timestamp'].max()}")

FEATURES = ['winding_temp', 'current', 'vibration', 'oil_level']
X = df[FEATURES].values

# ─────────────────────────────────────────────
#  FEATURE ENGINEERING
#  Add rolling stats — this is critical for transformers
#  A single reading means little; the trend matters
# ─────────────────────────────────────────────
df['temp_rolling_mean_10']  = df['winding_temp'].rolling(10).mean()
df['temp_rolling_std_10']   = df['winding_temp'].rolling(10).std()
df['current_rolling_mean_10'] = df['current'].rolling(10).mean()
df['vibration_rolling_max_10'] = df['vibration'].rolling(10).max()
df['temp_rate_of_change']   = df['winding_temp'].diff()   # delta per reading
df['current_rate_of_change'] = df['current'].diff()

df = df.dropna()  # drop NaN rows from rolling window
print(f"After feature engineering: {len(df)} rows")

FEATURES_ENG = [
  'winding_temp', 'current', 'vibration', 'oil_level',
  'temp_rolling_mean_10', 'temp_rolling_std_10',
  'current_rolling_mean_10', 'vibration_rolling_max_10',
  'temp_rate_of_change', 'current_rate_of_change'
]
X_eng = df[FEATURES_ENG].values

# ─────────────────────────────────────────────
#  SCALE
# ─────────────────────────────────────────────
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X_eng)
joblib.dump(scaler, 'scaler_phase1.pkl')
print("Scaler saved.")

# ─────────────────────────────────────────────
#  1. ISOLATION FOREST
# ─────────────────────────────────────────────
# contamination=0.05 means we expect ~5% of readings to be anomalous
# tune this based on your domain knowledge of the transformer
iso = IsolationForest(
    n_estimators=200,
    contamination=0.05,
    random_state=42,
    n_jobs=-1
)
iso.fit(X_scaled)
joblib.dump(iso, 'isolation_forest_phase1.pkl')

iso_scores = iso.decision_function(X_scaled)   # higher = more normal
iso_labels = iso.predict(X_scaled)             # 1=normal, -1=anomaly

df['iso_anomaly_score'] = iso_scores
df['iso_label'] = iso_labels

n_anomalies = (iso_labels == -1).sum()
print(f"[IsoForest] Anomalies detected: {n_anomalies} ({100*n_anomalies/len(df):.1f}%)")

# ─────────────────────────────────────────────
#  2. AUTOENCODER
#  Learns to reconstruct normal patterns
#  High reconstruction error = anomaly
# ─────────────────────────────────────────────
n_features = X_scaled.shape[1]

encoder_input = keras.Input(shape=(n_features,))
x = keras.layers.Dense(32, activation='relu')(encoder_input)
x = keras.layers.Dense(16, activation='relu')(x)
x = keras.layers.Dense(8,  activation='relu')(x)       # bottleneck
x = keras.layers.Dense(16, activation='relu')(x)
x = keras.layers.Dense(32, activation='relu')(x)
autoencoder_output = keras.layers.Dense(n_features, activation='linear')(x)

autoencoder = keras.Model(encoder_input, autoencoder_output)
autoencoder.compile(optimizer='adam', loss='mse')
autoencoder.summary()

# Train ONLY on data that IsoForest considers normal
# This is the key insight: don't train the autoencoder on anomalies
X_normal = X_scaled[iso_labels == 1]
print(f"Training autoencoder on {len(X_normal)} normal samples")

history = autoencoder.fit(
    X_normal, X_normal,
    epochs=100,
    batch_size=64,
    validation_split=0.1,
    callbacks=[
        keras.callbacks.EarlyStopping(patience=10, restore_best_weights=True),
        keras.callbacks.ReduceLROnPlateau(patience=5, factor=0.5)
    ],
    shuffle=True,
    verbose=1
)

autoencoder.save('autoencoder_phase1.keras')

# Reconstruction error on all data
X_reconstructed = autoencoder.predict(X_scaled)
mse_errors = np.mean(np.square(X_scaled - X_reconstructed), axis=1)
df['ae_reconstruction_error'] = mse_errors

# Threshold = mean + 3*std of reconstruction error on NORMAL data
normal_errors = mse_errors[iso_labels == 1]
ae_threshold = normal_errors.mean() + 3 * normal_errors.std()
print(f"[Autoencoder] Reconstruction error threshold: {ae_threshold:.6f}")

df['ae_anomaly'] = (mse_errors > ae_threshold).astype(int)

# ─────────────────────────────────────────────
#  COMBINED ANOMALY SCORE
#  Both models must agree for high confidence
# ─────────────────────────────────────────────
# Normalize iso score to [0,1] — higher = more anomalous
iso_norm = 1 - (iso_scores - iso_scores.min()) / (iso_scores.max() - iso_scores.min())
# Normalize AE error to [0,1]
ae_norm  = (mse_errors - mse_errors.min()) / (mse_errors.max() - mse_errors.min())

df['combined_anomaly_score'] = 0.5 * iso_norm + 0.5 * ae_norm
df['high_confidence_anomaly'] = ((iso_labels == -1) & (df['ae_anomaly'] == 1)).astype(int)

print(f"\nHigh-confidence anomalies (both models agree): {df['high_confidence_anomaly'].sum()}")

# ─────────────────────────────────────────────
#  SAVE RESULTS & THRESHOLDS
# ─────────────────────────────────────────────
df.to_csv('readings_phase1_scored.csv', index=False)

thresholds = {
    'ae_reconstruction_threshold': float(ae_threshold),
    'iso_contamination': 0.05,
    'features': FEATURES_ENG,
    'phase': 1
}
with open('thresholds_phase1.json', 'w') as f:
    json.dump(thresholds, f, indent=2)

print("\nPhase 1 complete. Files saved:")
print("  scaler_phase1.pkl")
print("  isolation_forest_phase1.pkl")
print("  autoencoder_phase1.keras")
print("  thresholds_phase1.json")
print("  readings_phase1_scored.csv  ← open this, review anomalies, add labels")