'use strict';
/**
 * The Python package is the price. This process spawns it and does not
 * recompute P. A failure is the exception class, never the child text.
 */

const { spawn } = require('node:child_process');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const TIMEOUT_MS = 20000;

function loadReading() {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn('python3', ['-m', 'bot.t2c'], {
        cwd: ROOT,
        env: process.env,
        shell: false,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (err) {
      reject(err instanceof Error ? err : new Error('Error'));
      return;
    }
    const out = [];
    let settled = false;
    const timer = setTimeout(() => {
      fail(new Error('TimeoutError'));
      child.kill();
    }, TIMEOUT_MS);
    function fail(err) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(err);
    }
    child.stdout.on('data', (chunk) => { out.push(chunk); });
    child.stderr.on('data', () => { /* class name only leaves this process */ });
    child.on('error', (err) => fail(err));
    child.on('close', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (code !== 0) {
        const err = new Error('ExitError');
        err.name = 'ExitError';
        reject(err);
        return;
      }
      try {
        resolve(JSON.parse(Buffer.concat(out).toString('utf8')));
      } catch (err) {
        const parse = new Error('SyntaxError');
        parse.name = 'SyntaxError';
        reject(parse);
      }
    });
  });
}

module.exports = { loadReading };
