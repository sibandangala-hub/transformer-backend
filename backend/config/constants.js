module.exports = {
  FEATURES: [
    'winding_temp', 'current', 'vibration', 'oil_level',
    'temp_rolling_mean_10', 'temp_rolling_std_10',
    'current_rolling_mean_10', 'vibration_rolling_max_10',
    'temp_rate_of_change', 'current_rate_of_change'
  ],
  LABEL_MAP: {
    0: 'Normal',
    1: 'Overheating',
    2: 'Overcurrent',
    3: 'Abnormal Vibration',
    4: 'Low Oil',
    5: 'Combined Fault'
  },
  SEVERITY_LEVELS: ['normal', 'low', 'medium', 'critical'],
  ENTROPY_THRESHOLD: 0.6,
  MIN_CONFIDENCE:    0.7
};