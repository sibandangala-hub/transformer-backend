# Firestore Schema

## /readings/{auto_id}
| Field              | Type    | Description                        |
|--------------------|---------|------------------------------------|
| winding_temp       | float   | DS18B20 winding temperature °C     |
| current            | float   | ACS712 RMS current A               |
| vibration          | float   | MPU6050 peak acceleration m/s²     |
| oil_level          | float   | HC-SR04 oil level %                |
| temp_fault         | bool    | DS18B20 read fault flag            |
| timestamp          | string  | ISO8601 UTC from NTP               |
| seq                | int     | ESP32 sequence counter             |
| uptime_ms          | int     | ESP32 millis()                     |
| label              | int     | 0=Normal 1=Overheat 2=Overcurrent 3=Vibration 4=LowOil 5=Combined |
| anomaly_score      | float   | Phase 1 combined anomaly score     |
| cluster            | int     | Phase 1 cluster assignment         |
| predicted_class    | int     | Phase 2 model prediction           |
| predicted_label    | string  | Human readable prediction          |
| confidence         | float   | Ensemble confidence 0-1            |
| severity           | string  | normal / low / medium / critical   |
| scored_at          | timestamp | When inference ran               |
| labeled_at         | timestamp | When human labeled               |
| created_at         | timestamp | Server write time                |

## /models/{model_id}
| Field       | Type      | Description                     |
|-------------|-----------|---------------------------------|
| version     | int       | Unix timestamp of training run  |
| type        | string    | Model architecture description  |
| trained_at  | timestamp | Training completion time        |
| rf_f1       | float     | Random Forest cross-val F1      |
| gb_f1       | float     | Gradient Boosting cross-val F1  |
| n_samples   | int       | Training set size               |
| reason      | string    | Why retrain was triggered       |
| active      | bool      | Is this the live model          |

## /alerts/{auto_id}
| Field         | Type      | Description                    |
|---------------|-----------|--------------------------------|
| reading_id    | string    | Ref to /readings               |
| alert_type    | string    | anomaly / fault_predicted      |
| label         | string    | Fault class name               |
| severity      | string    | low / medium / critical        |
| acknowledged  | bool      | Operator has seen it           |
| timestamp     | timestamp | Alert creation time            |

## /review_queue/{auto_id}
| Field       | Type      | Description                      |
|-------------|-----------|----------------------------------|
| reading_id  | string    | Ref to /readings                 |
| entropy     | float     | Model uncertainty score          |
| margin      | float     | P(1st) - P(2nd)                  |
| disagreement| bool      | RF and GB predicted different classes |
| status      | string    | pending / done / skipped         |
| created_at  | timestamp |                                  |

## /drift_events/{auto_id}
| Field       | Type      | Description                      |
|-------------|-----------|----------------------------------|
| feature     | string    | Which feature drifted            |
| ks_stat     | float     | KS test statistic                |
| p_value     | float     | KS test p-value                  |
| resolved    | bool      | Resolved by retraining           |
| resolved_at | timestamp |                                  |
| created_at  | timestamp |                                  |

## /retrain_log/{auto_id}
| Field      | Type      | Description                       |
|------------|-----------|-----------------------------------|
| status     | string    | success / aborted                 |
| reason     | string    | Trigger reason                    |
| new_f1     | float     | New model F1                      |
| old_f1     | float     | Previous model F1                 |
| timestamp  | timestamp |                                   |
