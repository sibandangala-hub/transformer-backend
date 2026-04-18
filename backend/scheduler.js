// scheduler.js  — add to your Render service as a background worker
const cron = require('node-cron');
const { execSync } = require('child_process');
const admin = require('firebase-admin');
const db = admin.firestore();

// Every hour — check for drift
cron.schedule('0 * * * *', async () => {
  console.log('[CRON] Running drift check...');
  try {
    execSync('python3 ml/drift_detector.py', { stdio: 'inherit' });
  } catch (e) {
    console.error('[CRON] Drift check failed:', e.message);
  }
});

// Every 6 hours — check if retraining conditions are met
cron.schedule('0 */6 * * *', async () => {
  console.log('[CRON] Checking retrain conditions...');
  try {
    execSync('python3 ml/auto_retrain.py', { stdio: 'inherit' });
  } catch (e) {
    console.error('[CRON] Retrain check failed:', e.message);
  }
});

// Every 30 seconds — run live inference on latest reading
cron.schedule('*/30 * * * * *', async () => {
  try {
    const latest = await db.collection('readings')
      .orderBy('created_at', 'desc').limit(1).get();

    if (latest.empty) return;

    const doc  = latest.docs[0];
    const data = doc.data();
    if (data.predicted_class != null) return;  // already scored

    // pull last 30 for sequence context
    const history = await db.collection('readings')
      .orderBy('created_at', 'desc').limit(30).get();
    const histArr = history.docs.reverse().map(d => d.data());

    const result = JSON.parse(
      execSync(`python3 ml/predict.py '${JSON.stringify(data)}' '${JSON.stringify(histArr)}'`)
        .toString()
    );

    await doc.reference.update({
      predicted_class:    result.predicted_class,
      predicted_label:    result.predicted_label,
      confidence:         result.confidence,
      severity:           result.severity,
      iso_anomaly:        result.iso_anomaly,
      ae_anomaly:         result.ae_anomaly,
      scored_at:          admin.firestore.FieldValue.serverTimestamp()
    });

    // Write alert if severity warrants it
    if (result.severity === 'critical' || result.severity === 'medium') {
      await db.collection('alerts').add({
        reading_id:  doc.id,
        alert_type:  'fault_predicted',
        label:       result.predicted_label,
        confidence:  result.confidence,
        severity:    result.severity,
        acknowledged: false,
        timestamp:   admin.firestore.FieldValue.serverTimestamp()
      });
    }

    // Active learning flag
    if (result.needs_label) {
      await db.collection('review_queue').add({
        reading_id: doc.id,
        entropy:    result.entropy,
        status:     'pending',
        created_at: admin.firestore.FieldValue.serverTimestamp()
      });
    }

  } catch (e) {
    console.error('[CRON] Inference failed:', e.message);
  }
});