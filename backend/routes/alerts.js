const express = require('express');
const router  = express.Router();
const admin   = require('../config/firebase');
const auth    = require('../middleware/auth');

const db = admin.firestore();

// GET /api/alerts — unacknowledged alerts
router.get('/', auth, async (req, res) => {
  try {
    const snap = await db.collection('alerts')
      .where('acknowledged', '==', false)
      .orderBy('timestamp', 'desc')
      .limit(50)
      .get();

    const alerts = snap.docs.map(d => ({ id: d.id, ...d.data() }));
    res.json({ count: alerts.length, alerts });
  } catch (err) {
    console.error('[ALERTS] Fetch failed:', err);
    res.status(500).json({ error: 'Fetch failed' });
  }
});

// PATCH /api/alerts/:id/ack — acknowledge alert
router.patch('/:id/ack', auth, async (req, res) => {
  try {
    await db.collection('alerts').doc(req.params.id).update({
      acknowledged:    true,
      acknowledged_at: admin.firestore.FieldValue.serverTimestamp()
    });
    res.json({ status: 'ok' });
  } catch (err) {
    res.status(500).json({ error: 'Acknowledge failed' });
  }
});

module.exports = router;