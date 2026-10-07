'use strict';

/**
 * RUNECLAW_STATE_DIR as the bot reads it.
 *
 * The bot anchors a relative state path at the repository root
 * (`bot/utils/paths.py::state_path`), never at the process's working
 * directory. The web process usually starts in `app/`, and two readers here
 * joined a relative value onto ITS cwd: `RUNECLAW_STATE_DIR=data`, the bot's
 * own default spelling, put the calibration curve at `app/data/...` while
 * the bot wrote `<repo>/data/...`, and the dashboard said "unmeasured" over
 * a fitted curve the engine was applying. One reading, for every reader.
 */
const os = require('os');
const path = require('path');

const REPO_ROOT = path.join(__dirname, '..', '..');

/** The configured state directory, absolute; '' when none is set. */
function explicitStateDir() {
  const raw = (process.env.RUNECLAW_STATE_DIR || '').trim();
  if (!raw) return '';
  const expanded = raw === '~' ? os.homedir()
    : raw.startsWith('~/') ? path.join(os.homedir(), raw.slice(2)) : raw;
  return path.isAbsolute(expanded) ? expanded : path.join(REPO_ROOT, expanded);
}

module.exports = { REPO_ROOT, explicitStateDir };
