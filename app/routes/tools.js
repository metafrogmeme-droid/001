'use strict';
/**
 * GET /api/tools.
 *
 * The card list is app/data/tools.json. That file has no admin link and no
 * date. The link to /t2c is added here, and only for the operator.
 */

const express = require('express');
const fs = require('node:fs');
const path = require('node:path');
const { optionalAuth } = require('../auth');
const { isOperator } = require('../lib/operator_view');

const router = express.Router();
router.use(optionalAuth);

function cards() {
  const file = path.join(__dirname, '..', 'data', 'tools.json');
  const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!Array.isArray(parsed)) return [];
  return parsed;
}

router.get('/', async (req, res) => {
  let admin = false;
  try {
    admin = await isOperator(req);
  } catch (err) {
    console.error('[tools] operator:', err && err.constructor ? err.constructor.name : 'Error');
    admin = false;
  }
  let list;
  try {
    list = cards();
  } catch (err) {
    console.error('[tools] list:', err && err.constructor ? err.constructor.name : 'Error');
    return res.status(500).json({ error: 'The list could not be read' });
  }
  const out = list.map((card) => {
    const copy = Object.assign({}, card);
    if (admin && copy.id === 't2c') copy.href = '/t2c';
    return copy;
  });
  res.set('Cache-Control', 'no-store').json({ tools: out });
});

module.exports = router;
