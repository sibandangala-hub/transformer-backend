require('dotenv').config();
require('./config/firebase');

const express = require('express');
const helmet  = require('helmet');
const cors    = require('cors');
const https   = require('https');

const app = express();
app.use(helmet());
app.use(cors());
app.use(express.json());

app.use('/api/data',   require('./routes/data'));
app.use('/api/export', require('./routes/export'));
app.use('/api/alerts', require('./routes/alerts'));
app.use('/api/label',  require('./routes/label'));

app.get('/health', (req, res) => {
  res.json({
    status:    'ok',
    uptime:    process.uptime(),
    timestamp: new Date().toISOString()
  });
});

app.use((req, res) => res.status(404).json({ error: 'Not found' }));
app.use((err, req, res, next) => {
  console.error('[SERVER ERROR]', err);
  res.status(500).json({ error: 'Internal server error' });
});

// Keepalive — prevents Render free tier cold start
setInterval(() => {
  https.get('https://transformer-pm-api.onrender.com/health', res => {
    console.log('[KEEPALIVE] ping:', res.statusCode);
  }).on('error', () => {});
}, 9 * 60 * 1000);

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`[SERVER] Running on port ${PORT}`);
  console.log(`[SERVER] Environment: ${process.env.NODE_ENV}`);
});