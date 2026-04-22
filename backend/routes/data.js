const express  = require('express');
const router   = express.Router();
const admin    = require('../config/firebase');
const auth     = require('../middleware/auth');
const validate = require('../middleware/validate');

const db = admin.firestore();

router.post('/', auth, validate, async (req, res) => {
  const {
    winding_temp, oil_temp, current, vibration,
    oil_level, voltage, real_power, apparent_power,
    reactive_power, power_factor,
    temp_fault, oil_temp_fault, voltage_fault,
    timestamp, seq, uptime_ms
  } = req.body;

  const parse = (v, fallback = 0) =>
    v != null && !isNaN(parseFloat(v)) ? parseFloat(v) : fallback;

  try {
    const docRef = await db.collection('readings').add({
      winding_temp:   parse(winding_temp),
      oil_temp:       parse(oil_temp),
      temp_delta:     parse(winding_temp) - parse(oil_temp),
      current:        parse(current),
      vibration:      parse(vibration),
      oil_level:      parse(oil_level),
      voltage:        parse(voltage),
      real_power:     parse(real_power),
      apparent_power: parse(apparent_power),
      reactive_power: parse(reactive_power),
      power_factor:   parse(power_factor),
      temp_fault:     temp_fault      || false,
      oil_temp_fault: oil_temp_fault  || false,
      voltage_fault:  voltage_fault   || false,
      timestamp:      timestamp       || new Date().toISOString(),
      seq:            seq             ?? null,
      uptime_ms:      uptime_ms       ?? null,
      label:          null,
      anomaly_score:  null,
      severity:       null,
      scored_at:      null,
      created_at:     admin.firestore.FieldValue.serverTimestamp()
    });

    console.log(`[DATA] ${docRef.id} | WT:${winding_temp} OT:${oil_temp} V:${voltage} I:${current} W:${real_power} PF:${power_factor}`);
    res.json({ status: 'ok', id: docRef.id });

  } catch (err) {
    console.error('[DATA] Failed:', err);
    res.status(500).json({ error: 'DB write failed' });
  }
});

module.exports = router;