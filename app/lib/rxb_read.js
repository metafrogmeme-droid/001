'use strict';
/**
 * The website does not keep a second solver. It asks the Python one.
 * The child gets a short environment, not the process environment, and
 * the symbol is the only argument. A failed read is unreadable.
 */

const { spawnSync } = require('node:child_process');
const path = require('node:path');

const REPO = path.join(__dirname, '..', '..');

function runCli(args) {
  let result;
  try {
    result = spawnSync('python3', ['-m', 'bot.rxb.cli', ...args], {
      cwd: REPO,
      encoding: 'utf8',
      timeout: 20000,
      env: {
        PATH: process.env.PATH || '',
        HOME: process.env.HOME || '',
        LANG: process.env.LANG || 'C.UTF-8',
        RXB_ENABLED: process.env.RXB_ENABLED || '',
      },
    });
  } catch (err) {
    console.error('rxb solver failed');
    return { readable: false, reason: 'solver_unreadable' };
  }
  if (!result || result.status !== 0 || !result.stdout) {
    console.error('rxb solver failed');
    return { readable: false, reason: 'solver_unreadable' };
  }
  try {
    return JSON.parse(result.stdout);
  } catch (err) {
    console.error('rxb solver failed');
    return { readable: false, reason: 'solver_unreadable' };
  }
}

function readPage() {
  return runCli(['page']);
}

function readNode(token) {
  return runCli(['node', String(token == null ? '' : token)]);
}

module.exports = { readPage, readNode, REPO };
