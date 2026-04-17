"""
ml/feature_engineer.py — Converts 5 raw sensor readings → 19 ML features.

Raw sensors (5):
    oil_temp, winding_temp, current, vibration, oil_level

Thermal / electrical (4):
    thermal_stress, temp_ratio, overload_idx, heat_dissipation

Oil-related (2):
    oil_health, oil_deficit

Interaction (2):
    therm_oil_interact, vibration_norm

Temporal (1):
    d_winding_temp   (rate of change)

Rolling-window stats — last 5 readings (5):
    roll_mean_wt, roll_std_wt
    roll_mean_vib, roll_std_vib
    roll_mean_curr
"""

from collections import deque
import numpy as np

FEATURE_NAMES = [
    # ── raw sensors ──────────────────────────────
    "oil_temp",          # 0
    "winding_temp",      # 1
    "current",           # 2
    "vibration",         # 3
    "oil_level",         # 4
    # ── thermal / electrical ─────────────────────
    "thermal_stress",    # 5   I² × (wt/65)
    "temp_ratio",        # 6   wt / ot
    "overload_idx",      # 7   how far above rated 15 A
    "heat_dissipation",  # 8   (wt - ot) / I
    # ── oil ─────────────────────────────────────
    "oil_health",        # 9   oil_level penalised by hot oil
    "oil_deficit",       # 10  shortage below 60 % fill
    # ── interaction ──────────────────────────────
    "therm_oil_interact",# 11  thermal_stress × oil availability
    "vibration_norm",    # 12  vib / 0.5 (threshold)
    # ── temporal ─────────────────────────────────
    "d_winding_temp",    # 13  Δ winding_temp per reading
    # ── rolling window (last 5) ──────────────────
    "roll_mean_wt",      # 14
    "roll_std_wt",       # 15
    "roll_mean_vib",     # 16
    "roll_std_vib",      # 17
    "roll_mean_curr",    # 18
]

N_FEATURES = len(FEATURE_NAMES)   # 19


class FeatureEngineer:
    """
    Stateful transformer — maintains rolling history across calls.
    Call reset() when starting from scratch (e.g. after a system reset).
    """

    def __init__(self, window: int = 5):
        self._window    = window
        self._hist_wt   = deque(maxlen=window)
        self._hist_vib  = deque(maxlen=window)
        self._hist_curr = deque(maxlen=window)
        self._prev_wt   = None

    def reset(self):
        self._hist_wt.clear()
        self._hist_vib.clear()
        self._hist_curr.clear()
        self._prev_wt = None

    # ── Main transform ────────────────────────────────────────────────────────
    def transform(self, r: dict) -> np.ndarray:
        """
        r: dict with keys oil_temp, winding_temp, current, vibration, oil_level
        Returns float32 array of shape (N_FEATURES,)
        """
        ot  = float(r["oil_temp"])
        wt  = float(r["winding_temp"])
        I   = float(r["current"])
        vib = float(r["vibration"])
        oil = float(r["oil_level"])

        # ── update rolling history ────────────────────────────────────────
        self._hist_wt.append(wt)
        self._hist_vib.append(vib)
        self._hist_curr.append(I)

        # ── thermal / electrical ──────────────────────────────────────────
        thermal_stress   = (I ** 2) * (wt / 65.0)
        temp_ratio       = wt / (ot + 0.1)
        overload_idx     = max(0.0, I - 15.0) / 5.0
        heat_dissipation = (wt - ot) / (I + 0.1)

        # ── oil ───────────────────────────────────────────────────────────
        oil_temp_penalty = max(0.0, (ot - 55.0) / 45.0)
        oil_health       = oil * max(0.0, 1.0 - oil_temp_penalty)
        oil_deficit      = max(0.0, 60.0 - oil) / 60.0

        # ── interactions ──────────────────────────────────────────────────
        therm_oil_interact = thermal_stress * (oil / 100.0)
        vibration_norm     = vib / 0.5

        # ── temporal ──────────────────────────────────────────────────────
        d_winding_temp = (wt - self._prev_wt) if self._prev_wt is not None else 0.0
        self._prev_wt  = wt

        # ── rolling stats ─────────────────────────────────────────────────
        a_wt   = np.array(self._hist_wt,   dtype=np.float32)
        a_vib  = np.array(self._hist_vib,  dtype=np.float32)
        a_curr = np.array(self._hist_curr, dtype=np.float32)

        roll_mean_wt   = float(a_wt.mean())
        roll_std_wt    = float(a_wt.std())   if len(a_wt)   > 1 else 0.0
        roll_mean_vib  = float(a_vib.mean())
        roll_std_vib   = float(a_vib.std())  if len(a_vib)  > 1 else 0.0
        roll_mean_curr = float(a_curr.mean())

        return np.array([
            ot, wt, I, vib, oil,
            thermal_stress, temp_ratio, overload_idx, heat_dissipation,
            oil_health, oil_deficit,
            therm_oil_interact, vibration_norm,
            d_winding_temp,
            roll_mean_wt, roll_std_wt,
            roll_mean_vib, roll_std_vib,
            roll_mean_curr,
        ], dtype=np.float32)

    def transform_batch(self, readings: list) -> np.ndarray:
        """Re-process a list of historical readings, rebuilding history from scratch."""
        self.reset()
        return np.vstack([self.transform(r) for r in readings])

    # ── Convenience: get just the 3 interpretable derived scalars ─────────────
    @staticmethod
    def get_derived(r: dict) -> dict:
        """Return the 3 derived values shown on the status card."""
        ot  = float(r.get("oil_temp",      25))
        wt  = float(r.get("winding_temp",  25))
        I   = float(r.get("current",        0))
        oil = float(r.get("oil_level",    100))

        thermal_stress   = (I ** 2) * (wt / 65.0)
        oil_temp_penalty = max(0.0, (ot - 55.0) / 45.0)
        oil_health       = oil * max(0.0, 1.0 - oil_temp_penalty)
        d_wt             = float(r.get("d_winding_temp", 0))
        return {
            "thermal_stress": round(thermal_stress, 4),
            "oil_health":     round(oil_health,     2),
            "d_winding_temp": round(d_wt,           4),
        }
