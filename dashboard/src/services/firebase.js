import { initializeApp } from 'firebase/app';
import { getFirestore } from 'firebase/firestore';

const firebaseConfig = {
  apiKey:            "AIzaSyCMIA79ATbzqcp56c5xFmdMMkBk0F0AZQQ",
  authDomain:        "mytransformer-4fd10.firebaseapp.com",
  projectId:         "mytransformer-4fd10",
  storageBucket:     "mytransformer-4fd10.firebasestorage.app",
  messagingSenderId: "326177867103",
  appId:             "1:326177867103:web:b00900c20308724adca2ea"
};

const app = initializeApp(firebaseConfig);
export const db  = getFirestore(app);