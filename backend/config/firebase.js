const admin = require('firebase-admin');
const path  = require('path');

let initialized = false;

function initFirebase() {
  if (initialized) return;

  // On Render, credentials come as an env var (JSON string)
  // Locally, they come from the file
  if (process.env.FIREBASE_CREDENTIALS_JSON) {
    const serviceAccount = JSON.parse(process.env.FIREBASE_CREDENTIALS_JSON);
    admin.initializeApp({
      credential: admin.credential.cert(serviceAccount)
    });
  } else {
    const credPath = path.resolve(process.env.FIREBASE_CREDENTIALS || './firebase-credentials.json');
    const serviceAccount = require(credPath);
    admin.initializeApp({
      credential: admin.credential.cert(serviceAccount)
    });
  }

  initialized = true;
  console.log('[Firebase] Initialized');
}

initFirebase();

module.exports = admin;