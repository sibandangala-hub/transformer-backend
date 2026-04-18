require('dotenv').config();
require('./config/firebase');   // init firebase immediately

const express = require('express');
const helmet  = require('helmet');
const cors    = require('cors');

const app = express();
app.use(helmet());
app.use(cors());
app.use(express.json());

// Routes
app.use('/api/data',    require('./routes/data'));
app.use('/api/export',  require('./routes/export'));
app.use('/api/alerts',  require('./routes/alerts'));
app.use('/api/label',   require('./routes/label'));

// Health check — Render pings this to keep service alive
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    uptime: process.uptime(),
    timestamp: new Date().toISOString()
  });
});

// 404
app.use((req, res) => res.status(404).json({ error: 'Not found' }));

// Global error handler
app.use((err, req, res, next) => {
  console.error('[SERVER ERROR]', err);
  res.status(500).json({ error: 'Internal server error' });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`[SERVER] Running on port ${PORT}`);
  console.log(`[SERVER] Environment: ${process.env.NODE_ENV}`);
});