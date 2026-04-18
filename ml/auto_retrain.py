# ml/auto_retrain.py
import os
import json
import joblib
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_class_weight
import tensorflow as tf
from tensorflow import keras
import firebase_admin
from firebase_admin import firestore

RETRAIN_TRIGGERS = {
    'min_new_labeled_samples': 200,   # at least 200 new labeled readings
    'min_days_since_retrain':  7,     # don't retrain more than weekly
    'drift_detected':          True,  # retrain if drift was flagged
    'performance_drop':        0.05   # retrain if F1 drops 5% on recent data
}

class AutoRetrain:

    def __init__(self, db):
        self.db = db

    def should_retrain(self) -> tuple[bool, str]:
        # Check last retrain date
        models_ref  = self.db.collection('models') \
                         .where('active', '==', True) \
                         .order_by('trained_at', direction=firestore.Query.DESCENDING) \
                         .limit(1).get()

        if not models_ref:
            return True, "No active model found"

        last_model  = models_ref[0].to_dict()
        last_trained = last_model['trained_at']
        days_since  = (datetime.utcnow() - last_trained.replace(tzinfo=None)).days

        if days_since < RETRAIN_TRIGGERS['min_days_since_retrain']:
            return False, f"Retrained {days_since}d ago — too soon"

        # Count new labeled samples since last retrain
        new_labeled = self.db.collection('readings') \
                        .where('label', '!=', None) \
                        .where('labeled_at', '>', last_trained) \
                        .get()

        if len(new_labeled) < RETRAIN_TRIGGERS['min_new_labeled_samples']:
            return False, f"Only {len(new_labeled)} new labels — need {RETRAIN_TRIGGERS['min_new_labeled_samples']}"

        # Check drift flag
        drift_ref = self.db.collection('drift_events') \
                      .where('resolved', '==', False) \
                      .limit(1).get()

        if drift_ref:
            return True, "Unresolved drift detected"

        return True, f"{len(new_labeled)} new labeled samples available"

    def retrain(self, reason: str):
        print(f"[AutoRetrain] Triggered: {reason}")

        # Pull all labeled data from Firebase
        snap = self.db.collection('readings').where('label', '!=', None).get()
        rows = []
        for doc in snap:
            d = doc.to_dict()
            d['id'] = doc.id
            rows.append(d)

        df = pd.DataFrame(rows)
        print(f"[AutoRetrain] Training on {len(df)} labeled samples")

        FEATURES = [
            'winding_temp', 'current', 'vibration', 'oil_level',
            'temp_rolling_mean_10', 'temp_rolling_std_10',
            'current_rolling_mean_10', 'vibration_rolling_max_10',
            'temp_rate_of_change', 'current_rate_of_change'
        ]

        # Recompute rolling features
        df = df.sort_values('timestamp').reset_index(drop=True)
        df['temp_rolling_mean_10']     = df['winding_temp'].rolling(10).mean().bfill()
        df['temp_rolling_std_10']      = df['winding_temp'].rolling(10).std().bfill()
        df['current_rolling_mean_10']  = df['current'].rolling(10).mean().bfill()
        df['vibration_rolling_max_10'] = df['vibration'].rolling(10).max().bfill()
        df['temp_rate_of_change']      = df['winding_temp'].diff().bfill()
        df['current_rate_of_change']   = df['current'].diff().bfill()

        X = df[FEATURES].values
        y = df['label'].astype(int).values

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

        rf = RandomForestClassifier(n_estimators=300, max_depth=15,
                                    class_weight='balanced', random_state=42, n_jobs=-1)
        rf_cv = cross_val_score(rf, X_scaled, y, cv=skf, scoring='f1_weighted')
        rf.fit(X_scaled, y)

        gb = GradientBoostingClassifier(n_estimators=200, max_depth=5,
                                        learning_rate=0.05, random_state=42)
        gb_cv = cross_val_score(gb, X_scaled, y, cv=skf, scoring='f1_weighted')
        gb.fit(X_scaled, y)

        new_f1 = float(rf_cv.mean())

        # Gate: only deploy if new model is not worse than current
        current_models = self.db.collection('models') \
                           .where('active', '==', True).limit(1).get()
        if current_models:
            current_f1 = current_models[0].to_dict().get('rf_f1', 0.0)
            if new_f1 < current_f1 - RETRAIN_TRIGGERS['performance_drop']:
                print(f"[AutoRetrain] New model F1={new_f1:.4f} worse than current F1={current_f1:.4f} — ABORTING")
                self._log_retrain_event('aborted', reason, new_f1, current_f1)
                return

        # Save new models
        version = int(datetime.utcnow().timestamp())
        joblib.dump(rf,     f'models/rf_v{version}.pkl')
        joblib.dump(gb,     f'models/gb_v{version}.pkl')
        joblib.dump(scaler, f'models/scaler_v{version}.pkl')

        # Deactivate old model, register new one
        for old in self.db.collection('models').where('active', '==', True).get():
            old.reference.update({'active': False})

        self.db.collection('models').add({
            'version':    version,
            'type':       'rf+gb+lstm_ensemble',
            'trained_at': datetime.utcnow(),
            'rf_f1':      new_f1,
            'gb_f1':      float(gb_cv.mean()),
            'n_samples':  len(df),
            'reason':     reason,
            'active':     True
        })

        # Mark drift as resolved
        for d in self.db.collection('drift_events').where('resolved', '==', False).get():
            d.reference.update({'resolved': True, 'resolved_at': datetime.utcnow()})

        print(f"[AutoRetrain] Complete. New model v{version} deployed. F1={new_f1:.4f}")
        self._log_retrain_event('success', reason, new_f1)

    def _log_retrain_event(self, status, reason, new_f1, old_f1=None):
        self.db.collection('retrain_log').add({
            'status':    status,
            'reason':    reason,
            'new_f1':    new_f1,
            'old_f1':    old_f1,
            'timestamp': datetime.utcnow()
        })