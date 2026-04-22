module.exports = function validate(req, res, next) {
  const { winding_temp, current, vibration } = req.body;

  if (winding_temp == null || current == null || vibration == null) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  const inRange = (v, min, max) =>
    v == null || (typeof parseFloat(v) === 'number' && parseFloat(v) >= min && parseFloat(v) <= max);

  if (!inRange(parseFloat(winding_temp), -10, 150))
    return res.status(400).json({ error: 'winding_temp out of range' });
  if (!inRange(parseFloat(current), 0, 50))
    return res.status(400).json({ error: 'current out of range' });
  if (!inRange(parseFloat(vibration), 0, 100))
    return res.status(400).json({ error: 'vibration out of range' });

  next();
};