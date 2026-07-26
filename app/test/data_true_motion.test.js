'use strict';
/**
 * Data-true motion — the escape circuit and the heat ember. The rules these
 * animations must obey:
 * - They animate REAL values only (plan order, heat percent) and invent
 *   nothing.
 * - prefers-reduced-motion neutralizes both (CSS-level, so even the JS
 *   stagger becomes a visual no-op).
 * - Re-renders that don't change the data must not restrobe: the circuit
 *   cascades only when the PLAN changes shape; the ember glides from the
 *   last rendered value instead of restarting at zero.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

test('escape circuit: cascades in plan order, only when the plan changed', () => {
  const page = read('public', 'escape.html');
  assert.match(page, /\.step\.dim \{ opacity: \.22; \}/);
  assert.match(page, /prefers-reduced-motion: reduce\) \{ \.step \{ transition: none; \} \.step\.dim \{ opacity: 1; \}/,
    'reduced motion renders every step fully lit — the JS stagger becomes a no-op');
  assert.match(page, /planKey !== lastPlanKey/,
    'retyping a symbol must not restrobe the list');
  assert.match(page, /cascade \? ' dim' : ''/);
  assert.match(page, /140 \+ i \* 130/, 'the stagger IS the plan order');
});

test('heat ember: fills to the real percent, glides between reads, warms honestly', () => {
  const page = read('public', 'arena.html');
  assert.match(page, /\.heatbar \.hfill[\s\S]{0,200}transition: width/);
  assert.match(page, /prefers-reduced-motion: reduce\) \{ \.heatbar \.hfill \{ transition: none; \}/);
  assert.match(page, /Math\.min\(h\.pct, 100\)/, 'the fill is the real heat percent, capped at the bar');
  assert.match(page, /Math\.min\(lastHeatPct \|\| 0, 100\)/,
    'refreshes glide from the LAST rendered value, never restart at zero');
  assert.match(page, /h\.pct > 5 \? '#ff5c6c' : h\.pct > 2 \? '#ff9f45' : '#e6c34b'/,
    'the warmth thresholds are visible in one place');
  assert.match(page, /aria-hidden="true"><span class="hfill"/,
    'the bar is decoration beside the stated number, not a replacement for it');
});
