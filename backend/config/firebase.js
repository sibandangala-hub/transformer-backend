const admin = require('firebase-admin');

let initialized = false;

function initFirebase() {
  if (initialized) return;

  if (!process.env.FIREBASE_CREDENTIALS_JSON) {
    throw new Error('FIREBASE_CREDENTIALS_JSON environment variable is not set');
  }

  try {
    const serviceAccount = JSON.parse(process.env.FIREBASE_CREDENTIALS_JSON);
    admin.initializeApp({
      credential: admin.credential.cert(serviceAccount)
    });
    initialized = true;
    console.log('[Firebase] Initialized successfully');
  } catch (err) {
    throw new Error('Failed to parse FIREBASE_CREDENTIALS_JSON: ' + err.message);
  }
}

initFirebase();

module.exports = admin;