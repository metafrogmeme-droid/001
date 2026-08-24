'use strict';
/**
 * The hero mascot's cost and its glow, both pinned.
 *
 * The landing page shipped `agent.glb` — byte-identical to the `_Luminous`
 * variant, the one cut of the model built with DOUBLE the emissive strength of
 * every other, and the heaviest at 1.5 MB. Nobody chose that; the filename
 * `agent.glb` says nothing about which variant it is a copy of, so the loudest
 * asset became the default by looking like a neutral one.
 *
 * That is the failure this file exists to prevent, and it is not a styling
 * opinion: the glow budget is a NUMBER inside the asset, so it can be read and
 * asserted. A future edit that repoints MODEL_URL at Luminous — or drops a new
 * `agent.glb` that happens to be Luminous again — fails here rather than
 * shipping and being noticed on somebody's phone weeks later.
 *
 * The size assertions are the same argument in the other currency. 2.15 MB of
 * three.js plus model was downloading to every visitor including phones, to
 * render decoration that sat above the headline.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const MASCOT = path.join(PUB, 'mascot');
const SRC = fs.readFileSync(path.join(PUB, 'js', 'mascot3d.js'), 'utf8');

/** The JSON chunk of a .glb — where materials and their emissive strength live. */
function glbJson(file) {
  const b = fs.readFileSync(file);
  if (b.readUInt32LE(0) !== 0x46546c67) return null;      // not glTF-binary
  let off = 12;
  while (off < b.length) {
    const len = b.readUInt32LE(off);
    const type = b.readUInt32LE(off + 4);
    if (type === 0x4e4f534a) return JSON.parse(b.slice(off + 8, off + 8 + len).toString('utf8'));
    off += 12 + len - 4;
  }
  return null;
}

/** The loudest emissive strength any material in the model declares. */
function maxGlow(json) {
  return Math.max(0, ...(json.materials || []).map(
    (m) => (((m.extensions || {}).KHR_materials_emissive_strength) || {}).emissiveStrength || 0));
}

/** Which file MODEL_URL actually points at, query string stripped. */
function modelPath() {
  const m = SRC.match(/const MODEL_URL = '([^']+)'/);
  assert.ok(m, 'MODEL_URL is no longer a single quoted literal — this file cannot read it');
  return path.join(PUB, m[1].split('?')[0].replace(/^\//, ''));
}

// ── the glow budget ───────────────────────────────────────────────────────

test('the shipped model is not the double-glow variant', () => {
  const file = modelPath();
  assert.ok(fs.existsSync(file), `MODEL_URL points at ${file}, which is not committed`);
  const glow = maxGlow(glbJson(file));
  assert.ok(glow <= 5,
    `the hero model declares emissive strength ${glow}. Luminous is the 10 — it is `
    + 'the cut built to glow twice as hard as the others, and it shipped once by '
    + 'being copied to the neutral-sounding name agent.glb. Pick base, Premium or '
    + 'Hero, or raise this ceiling deliberately with a reason.');
});

test('Luminous really is the loud one — the ceiling is not vacuous', () => {
  // A budget nothing can exceed asserts nothing. This proves the number above
  // discriminates: the variant it excludes is present and does exceed it.
  const lum = path.join(MASCOT, 'RUNECLAW_Command_Core_Mascot_Luminous.glb');
  if (!fs.existsSync(lum)) return;                        // variant retired; fine
  assert.ok(maxGlow(glbJson(lum)) > 5,
    'Luminous no longer exceeds the ceiling, so the test above would pass for it '
    + 'too and is no longer checking anything');
});

test('every committed variant is readable and declares its glow', () => {
  // A .glb this scan cannot parse would silently pass the ceiling above with
  // maxGlow 0 — an unreadable asset reported as the calmest one.
  const glbs = fs.readdirSync(MASCOT).filter((f) => f.endsWith('.glb'));
  assert.ok(glbs.length >= 2, 'the variant set has vanished');
  for (const f of glbs) {
    const j = glbJson(path.join(MASCOT, f));
    assert.ok(j, `${f} is not parseable as glTF-binary; its glow cannot be checked`);
    assert.ok(Array.isArray(j.materials) && j.materials.length,
      `${f} declares no materials — an empty read would score as zero glow`);
  }
});

// ── the download cost ─────────────────────────────────────────────────────

test('the hero model stays under a megabyte', () => {
  const bytes = fs.statSync(modelPath()).size;
  assert.ok(bytes < 1024 * 1024,
    `the hero model is ${Math.round(bytes / 1024)} KB. It loads on the landing `
    + 'page beside the headline; Luminous at 1.5 MB is what this ceiling exists '
    + 'to keep out.');
});

// ── the mobile gate, which is where the 2.15 MB actually went ─────────────

test('a phone is not asked to download the viewer at all', () => {
  // The gate is the whole saving: three.js (671 KB) plus the model never leave
  // the server for a narrow viewport. Pinned structurally because the failure
  // is invisible — the page still looks right on a phone with the gate removed,
  // it is just multiple megabytes heavier.
  assert.match(SRC, /function deviceWantsWebGL/,
    'the viewport gate is gone; every phone downloads three.js and the model again');
  assert.match(SRC, /innerWidth[\s\S]{0,200}?900/,
    'the gate no longer refuses narrow viewports');
  assert.match(SRC, /saveData/,
    'the gate ignores save-data, which is a visitor asking for exactly this');
});

test('the gate is consulted BEFORE the model is probed', () => {
  // modelReady() is itself a network request. Asking a phone to HEAD an asset
  // it will never render is a cost with no upside, and it is the kind of thing
  // that survives a refactor because nothing looks wrong.
  const gateAt = SRC.indexOf('hosts.filter(deviceWantsWebGL)');
  const probeAt = SRC.indexOf('await modelReady()', SRC.indexOf('export async function autoMount'));
  assert.ok(gateAt > 0, 'autoMount no longer filters hosts through the gate');
  assert.ok(probeAt > gateAt,
    'the model is probed before the gate decides — a phone still pays a request');
});

test('the dashboard avatar is exempt from the gate', () => {
  // It is small, inside an authenticated app the visitor chose to open, and it
  // carries state: it reacts while the agent is thinking. That is content, and
  // the argument for refusing decoration does not reach it.
  const fn = SRC.slice(SRC.indexOf('function deviceWantsWebGL'));
  const body = fn.slice(0, fn.indexOf('\n}'));
  assert.match(body, /avatar'\)\s*return true/,
    'the avatar is being refused on narrow viewports, which silently removes a '
    + 'reactive dashboard element rather than a decorative one');
});

// ── motion, the other half of "too much" ──────────────────────────────────

test('the hero does not spin at full speed', () => {
  const m = SRC.match(/autoRotateSpeed\s*=\s*([\d.]+)/);
  assert.ok(m, 'autoRotateSpeed is no longer a literal this test can read');
  assert.ok(Number(m[1]) <= 0.5,
    `autoRotateSpeed is ${m[1]}. Motion is the strongest signal on a page and `
    + 'this one is spent on decoration standing next to the headline.');
});

test('reduced-motion still stops it entirely', () => {
  // Slowing the spin must not have become a substitute for honouring the
  // preference. Slow motion is still motion.
  assert.match(SRC, /prefers-reduced-motion/);
  assert.match(SRC, /controls\.autoRotate = !reduce/,
    'auto-rotation no longer checks the reduced-motion preference');
});

test('the showcase viewer is exposed at neutral', () => {
  // 1.15 pushed an already-emissive model into bloom beside a headline it has
  // to not outshine.
  assert.match(SRC, /toneMappingExposure = avatar \? 1\.15 : 1(\.0)?/,
    'the showcase exposure is back above neutral, or the avatar lost its lift');
});
