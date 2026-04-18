// routes/export.js
const express = require('express');
const router  = express.Router();
const admin   = require('firebase-admin');
const db      = admin.firestore();

router.get('/csv', async (req, res) => {
  const key = req.headers['x-api-key'];
  if (key !== process.env.API_KEY) return res.status(401).json({ error: 'Unauthorized' });

  const { from, to, labeled_only } = req.query;

  let query = db.collection('readings').orderBy('created_at');
  if (from) query = query.where('created_at', '>=', new Date(from));
  if (to)   query = query.where('created_at', '<=', new Date(to));
  if (labeled_only === 'true') query = query.where('label', '!=', null);

  const snap = await query.get();
  const rows = [];

  rows.push('id,winding_temp,current,vibration,oil_level,temp_fault,timestamp,label,anomaly_score,cluster');

  snap.forEach(doc => {
    const d = doc.data();
    rows.push([
      doc.id,
      d.winding_temp, d.current, d.vibration, d.oil_level,
      d.temp_fault ? 1 : 0,
      d.timestamp,
      d.label ?? '',
      d.anomaly_score ?? '',
      d.cluster ?? ''
    ].join(','));
  });

  res.setHeader('Content-Type', 'text/csv');
  res.setHeader('Content-Disposition', 'attachment; filename=readings.csv');
  res.send(rows.join('\n'));
});

module.exports = router;