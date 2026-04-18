// routes/data.js
const express = require('express');
const router  = express.Router();
const admin   = require('firebase-admin');
const db      = admin.firestore();

router.post('/', async (req, res) => {
  const key = req.headers['x-api-key'];
  if (key !== process.env.API_KEY) return res.status(401).json({ error: 'Unauthorized' });

  const { winding_temp, current, vibration, oil_level,
          temp_fault, timestamp, seq, uptime_ms } = req.body;

  // basic sanity validation
  if (winding_temp == null || current == null || vibration == null || oil_level == null) {
    return res.status(400).json({ error: 'Missing fields' });
  }

  try {
    await db.collection('readings').add({
      winding_temp:  parseFloat(winding_temp),
      current:       parseFloat(current),
      vibration:     parseFloat(vibration),
      oil_level:     parseFloat(oil_level),
      temp_fault:    temp_fault || false,
      timestamp:     timestamp || new Date().toISOString(),
      seq:           seq || null,
      uptime_ms:     uptime_ms || null,
      label:         null,          // to be filled in Phase 2
      anomaly_score: null,          // to be filled in Phase 1 ML
      cluster:       null,          // to be filled in Phase 1 ML
      created_at:    admin.firestore.FieldValue.serverTimestamp()
    });

    res.json({ status: 'ok' });
  } catch (err) {
    console.error('[Firebase]', err);
    res.status(500).json({ error: 'DB write failed' });
  }
});

module.exports = router;