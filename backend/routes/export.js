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
    const rows = ['id,winding_temp,current,vibration,oil_level,temp_fault,timestamp,seq,label,anomaly_score,cluster'];

    snap.forEach(doc => {
      const d = doc.data();
      rows.push([
        doc.id,
        d.winding_temp,
        d.current,
        d.vibration,
        d.oil_level,
        d.temp_fault ? 1 : 0,
        d.timestamp,
        d.seq          ?? '',
        d.label        ?? '',
        d.anomaly_score ?? '',
        d.cluster      ?? ''
      ].join(','));
    });

    console.log(`[EXPORT] Exported ${snap.size} readings`);
    res.setHeader('Content-Type', 'text/csv');
    res.setHeader('Content-Disposition', 'attachment; filename=readings.csv');
    res.send(rows.join('\n'));

  } catch (err) {
    console.error('[EXPORT] Failed:', err);
    res.status(500).json({ error: 'Export failed' });
  }
});

module.exports = router;