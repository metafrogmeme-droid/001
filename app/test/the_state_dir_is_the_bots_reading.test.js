/**
 * RUNECLAW_STATE_DIR is read the way the bot reads it: a relative value is
 * anchored at the repository root (`bot/utils/paths.py::state_path`), not at
 * this process's cwd. The web process usually starts in `app/`, and with the
 * bot's own default spelling (`data`) the calibration curve was looked for
 * under `app/data/` while the bot wrote `<repo>/data/`, so the dashboard said
 * "unmeasured" over a curve the engine was applying.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { codeOnly } = require('./helpers/code_only');

const { REPO_ROOT, explicitStateDir } = require('../lib/state_dir');
const curve = require('../lib/calibration_curve');
const vault = require('../lib/secrets_vault');

function withEnv(value, cwd, fn) {
  const saved = process.env.RUNECLAW_STATE_DIR;
  const savedCwd = process.cwd();
  if (value === undefined) delete process.env.RUNECLAW_STATE_DIR;
  else process.env.RUNECLAW_STATE_DIR = value;
  if (cwd) process.chdir(cwd);
  try { return fn(); } finally {
    process.chdir(savedCwd);
    if (saved === undefined) delete process.env.RUNECLAW_STATE_DIR;
    else process.env.RUNECLAW_STATE_DIR = saved;
  }
}

const APP = path.join(REPO_ROOT, 'app');

test('a relative value is anchored at the repo root, from app/ as cwd', () => {
  withEnv('data', APP, () => {
    assert.strictEqual(explicitStateDir(), path.join(REPO_ROOT, 'data'));
    assert.deepStrictEqual(curve.candidateFiles(),
      [path.join(REPO_ROOT, 'data', curve.FILE_NAME)]);
    assert.strictEqual(vault.stateDirCandidates()[0], path.join(REPO_ROOT, 'data'));
  });
});

test('an absolute value is used as given, and ~ is the home directory', () => {
  const abs = path.join(os.tmpdir(), 'rc-state');
  withEnv(abs, APP, () => assert.strictEqual(explicitStateDir(), abs));
  withEnv('~/rc', APP, () => assert.strictEqual(explicitStateDir(), path.join(os.homedir(), 'rc')));
  withEnv('  ', APP, () => assert.strictEqual(explicitStateDir(), ''));
  withEnv(undefined, APP, () => assert.strictEqual(explicitStateDir(), ''));
});

test('the curve the bot wrote is read from app/ under a relative state dir', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-cal-'));
  const file = path.join(dir, curve.FILE_NAME);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, '{}');
  const relative = path.relative(REPO_ROOT, dir);
  withEnv(relative, APP, () => {
    assert.deepStrictEqual(curve.candidateFiles(), [file]);
    // Found and read: `{}` is no curve, which is `unavailable`. Looked for
    // under app/, the same file was `absent`.
    assert.strictEqual(curve.readCalibrationCurve().state, 'unavailable');
  });
  fs.rmSync(dir, { recursive: true, force: true });
});

test('nothing else in app/ reads the variable itself', () => {
  const offenders = [];
  const walk = (d) => {
    for (const name of fs.readdirSync(d)) {
      if (name === 'node_modules' || name === 'test') continue;
      const p = path.join(d, name);
      const st = fs.statSync(p);
      if (st.isDirectory()) walk(p);
      else if (p.endsWith('.js') && !p.endsWith(path.join('lib', 'state_dir.js'))
        && /process\.env\.RUNECLAW_STATE_DIR/.test(codeOnly(fs.readFileSync(p, 'utf8')))) {
        offenders.push(path.relative(REPO_ROOT, p));
      }
    }
  };
  walk(APP);
  assert.deepStrictEqual(offenders, []);
});
