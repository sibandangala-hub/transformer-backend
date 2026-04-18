# train_phase2.py
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.utils.class_weight import compute_class_weight
import tensorflow as tf
from tensorflow import keras
import joblib
import json
import matplotlib.pyplot as plt
import seaborn as sns

# ─────────────────────────────────────────────
#  LOAD LABELED DATA
# ─────────────────────────────────────────────
df = pd.read_csv('readings_phase1_scored.csv')
df_labeled = df[df['label'].notna()].copy()
print(f"Labeled samples: {len(df_labeled)}")
print(df_labeled['label'].value_counts())

# Ensure minimum class representation
class_counts = df_labeled['label'].value_counts()
if (class_counts < 30).any():
    print("WARNING: Some classes have < 30 samples. Consider collecting more labeled data.")

FEATURES_ENG = [
  'winding_temp', 'current', 'vibration', 'oil_level',
  'temp_rolling_mean_10', 'temp_rolling_std_10',
  'current_rolling_mean_10', 'vibration_rolling_max_10',
  'temp_rate_of_change', 'current_rate_of_change'
]

X = df_labeled[FEATURES_ENG].values
y = df_labeled['label'].astype(int).values

# ─────────────────────────────────────────────
#  HANDLE CLASS IMBALANCE
#  Normal readings will vastly outnumber fault readings
# ─────────────────────────────────────────────
classes = np.unique(y)
weights = compute_class_weight('balanced', classes=classes, y=y)
class_weight_dict = dict(zip(classes.astype(int), weights))
print(f"Class weights: {class_weight_dict}")

scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)
joblib.dump(scaler, 'scaler_phase2.pkl')

# ─────────────────────────────────────────────
#  MODEL 1: RANDOM FOREST
#  Fast inference, interpretable feature importance
#  This is your PRIMARY model for production
# ─────────────────────────────────────────────
rf = RandomForestClassifier(
    n_estimators=300,
    max_depth=15,
    min_samples_leaf=5,
    class_weight='balanced',
    random_state=42,
    n_jobs=-1
)

# Stratified K-Fold because your classes are imbalanced
skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
cv_scores = cross_val_score(rf, X_scaled, y, cv=skf, scoring='f1_weighted', n_jobs=-1)
print(f"\n[RF] Cross-val F1 (weighted): {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

rf.fit(X_scaled, y)
joblib.dump(rf, 'random_forest_phase2.pkl')

# Feature importance — critical for debugging
importances = pd.Series(rf.feature_importances_, index=FEATURES_ENG)
importances.sort_values().plot(kind='barh', figsize=(8, 5), title='RF Feature Importance')
plt.tight_layout()
plt.savefig('feature_importance.png')
plt.close()

# ─────────────────────────────────────────────
#  MODEL 2: GRADIENT BOOSTING
#  More accurate than RF, used as second opinion
# ─────────────────────────────────────────────
gb = GradientBoostingClassifier(
    n_estimators=200,
    max_depth=5,
    learning_rate=0.05,
    subsample=0.8,
    random_state=42
)
cv_scores_gb = cross_val_score(gb, X_scaled, y, cv=skf, scoring='f1_weighted', n_jobs=-1)
print(f"[GB] Cross-val F1 (weighted): {cv_scores_gb.mean():.4f} ± {cv_scores_gb.std():.4f}")
gb.fit(X_scaled, y)
joblib.dump(gb, 'gradient_boosting_phase2.pkl')

# ─────────────────────────────────────────────
#  MODEL 3: LSTM (for sequence anomaly detection)
#  RF/GB see each reading in isolation
#  LSTM sees the last N readings as a sequence
#  This catches gradual degradation patterns
# ─────────────────────────────────────────────
SEQ_LEN = 20   # look back 20 readings = 40 seconds at 2s interval

def build_sequences(X, y, seq_len):
    Xs, ys = [], []
    for i in range(seq_len, len(X)):
        Xs.append(X[i-seq_len:i])
        ys.append(y[i])
    return np.array(Xs), np.array(ys)

# Only build sequences from labeled data in temporal order
X_seq, y_seq = build_sequences(X_scaled, y, SEQ_LEN)
print(f"\n[LSTM] Sequence dataset: {X_seq.shape}")

n_classes = len(np.unique(y))
y_seq_cat = keras.utils.to_categorical(y_seq, num_classes=n_classes)

lstm_model = keras.Sequential([
    keras.layers.LSTM(64, return_sequences=True, input_shape=(SEQ_LEN, X_scaled.shape[1])),
    keras.layers.Dropout(0.3),
    keras.layers.LSTM(32),
    keras.layers.Dropout(0.3),
    keras.layers.Dense(32, activation='relu'),
    keras.layers.Dense(n_classes, activation='softmax')
])

lstm_model.compile(
    optimizer=keras.optimizers.Adam(0.001),
    loss='categorical_crossentropy',
    metrics=['accuracy']
)
lstm_model.summary()

class_weight_seq = {k: float(v) for k, v in class_weight_dict.items()}
lstm_model.fit(
    X_seq, y_seq_cat,
    epochs=80,
    batch_size=32,
    validation_split=0.15,
    class_weight=class_weight_seq,
    callbacks=[
        keras.callbacks.EarlyStopping(patience=10, restore_best_weights=True),
        keras.callbacks.ReduceLROnPlateau(patience=5, factor=0.5)
    ],
    verbose=1
)

lstm_model.save('lstm_phase2.keras')

# ─────────────────────────────────────────────
#  SAVE METADATA
# ─────────────────────────────────────────────
metadata = {
    'features': FEATURES_ENG,
    'n_classes': int(n_classes),
    'class_labels': {
        '0': 'Normal',
        '1': 'Overheating',
        '2': 'Overcurrent',
        '3': 'Abnormal Vibration',
        '4': 'Low Oil',
        '5': 'Combined Fault'
    },
    'seq_len': SEQ_LEN,
    'rf_cv_f1': float(cv_scores.mean()),
    'gb_cv_f1': float(cv_scores_gb.mean()),
    'phase': 2
}
with open('model_metadata_phase2.json', 'w') as f:
    json.dump(metadata, f, indent=2)

print("\nPhase 2 complete. Models saved.")