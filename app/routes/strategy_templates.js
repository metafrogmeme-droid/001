'use strict';
/**
 * PUBLIC strategy archetype templates — curated starting points for the
 * builder, served with NO auth. The catalogue is static per deploy, so it is
 * long-cacheable. Each template is a CONFIG in the community rule vocabulary
 * (§4: percent/ratio/count/list only — no dollar figures, no performance
 * claims) and is pinned by test to pass the same validateStrategy gate a
 * member's own draft passes.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { TEMPLATES } = require('../lib/strategy_templates');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

router.get('/', (req, res) => {
  res.setHeader('Cache-Control', 'public, max-age=3600');
  res.json({ templates: TEMPLATES, count: TEMPLATES.length });
});

module.exports = router;
