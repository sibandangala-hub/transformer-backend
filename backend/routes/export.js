const express = require('express');
const router  = express.Router();
const admin   = require('../config/firebase');
const auth    = require('../middleware/auth');

const db = admin.firestore();

router.get('/csv', auth, async (req, res) => {
  try {
    const { from, to, labeled_only } = req.query;

    let query = db.collection('readings').orderBy('created_at');
    if (from) query = query.where('created_at', '>=', new Date(from));
    if (to)   query = query.where('created_at', '<=', new Date(to));
    if (labeled_only === 'true') query = query.where('label', '!=', null);

    const snap = await query.get();

    const header = [
      'id', 'winding_temp', 'oil_temp', 'temp_delta',
      'current', 'vibration', 'oil_level',
      'voltage', 'real_power', 'apparent_power',
      'reactive_power', 'power_factor',
      'temp_fault', 'oil_temp_fault', 'voltage_fault',
      'timestamp', 'seq', 'label', 'anomaly_score', 'severity'
    ].join(',');

    const rows = [header];

    snap.forEach(doc => {
      const d = doc.data();
      rows.push([
        doc.id,
        d.winding_temp   ?? '',
        d.oil_temp       ?? '',
        d.temp_delta     ?? '',
        d.current        ?? '',
        d.vibration      ?? '',
        d.oil_level      ?? '',
        d.voltage        ?? '',
        d.real_power     ?? '',
        d.apparent_power ?? '',
        d.reactive_power ?? '',
        d.power_factor   ?? '',
        d.temp_fault     ? 1 : 0,
        d.oil_temp_fault ? 1 : 0,
        d.voltage_fault  ? 1 : 0,
        d.timestamp      ?? '',
        d.seq            ?? '',
        d.label          ?? '',
        d.anomaly_score  ?? '',
        d.severity       ?? ''
      ].join(','));
    });

    console.log(`[EXPORT] ${snap.size} readings`);
    res.setHeader('Content-Type', 'text/csv');
    res.setHeader('Content-Disposition', 'attachment; filename=readings.csv');
    res.send(rows.join('\n'));

  } catch (err) {
    console.error('[EXPORT] Failed:', err);
    res.status(500).json({ error: 'Export failed' });
  }
});

module.exports = router;