module.exports = function validate(req, res, next) {
  const { winding_temp, current, vibration, oil_level } = req.body;

  if (winding_temp == null || current == null ||
      vibration == null   || oil_level == null) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  const inRange = (v, min, max) => typeof v === 'number' && v >= min && v <= max;

  if (!inRange(winding_temp, -10, 120)) return res.status(400).json({ error: 'winding_temp out of range' });
  if (!inRange(current,       0,  50))  return res.status(400).json({ error: 'current out of range' });
  if (!inRange(vibration,     0,  50))  return res.status(400).json({ error: 'vibration out of range' });
  if (!inRange(oil_level,     0, 100))  return res.status(400).json({ error: 'oil_level out of range' });

  next();
};