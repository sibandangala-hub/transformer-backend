const express = require('express');
const router  = express.Router();
const admin   = require('../config/firebase');
const auth    = require('../middleware/auth');

const db = admin.firestore();

router.patch('/:id', auth, async (req, res) => {
  const { label } = req.body;
  if (label == null || ![0,1,2,3,4,5].includes(parseInt(label))) {
    return res.status(400).json({ error: 'label must be 0–5' });
  }
  try {
    await db.collection('readings').doc(req.params.id).update({
      label:      parseInt(label),
      labeled_at: admin.firestore.FieldValue.serverTimestamp()
    });

    // Mark review_queue item as done
    const qSnap = await db.collection('review_queue')
      .where('reading_id', '==', req.params.id)
      .where('status', '==', 'pending').get();
    qSnap.forEach(d => d.reference.update({ status: 'done' }));

    res.json({ status: 'ok' });
  } catch (err) {
    res.status(500).json({ error: 'Label update failed' });
  }
});

module.exports = router;