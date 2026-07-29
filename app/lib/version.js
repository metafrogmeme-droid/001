'use strict';
/**
 * Build/version stamp — so anyone (the operator, a test, the /status page, or
 * a monitoring probe) can see EXACTLY which commit the running server is
 * serving. A stale deploy then becomes a five-second check instead of
 * route-probing the site. Resolved ONCE at process start and memoised.
 *
 * §F-15: carries NO secrets and reads no credentials — only the short git SHA,
 * the commit time and the process boot time. All of that is public metadata.
 *
 * Resolution order (first hit wins), chosen so the stamp survives every deploy
 * shape we use:
 *   1. BUILD_SHA / SOURCE_COMMIT env (set by a container build ARG)
 *   2. build-info.json written at build time (a .git-less image still stamps)
 *   3. `git` in the checkout (the re-clone deploy path has a real .git)
 *   4. the .git plumbing files directly — HEAD → refs → packed-refs — for a
 *      re-clone deploy that has a .git dir but NO git binary on PATH
 *   5. 'unknown' — honest when nothing is available, never a fake value
 */

const { execFileSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.join(__dirname, '..', '..');

function gitOut(args) {
  try {
    const out = execFileSync('git', args, {
      cwd: REPO_ROOT,
      timeout: 1500,
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    return String(out).trim() || null;
  } catch (e) {
    return null; // no git binary / not a checkout — fall through honestly
  }
}

function readBuildFile() {
  // A build step may drop build-info.json next to the app or at the repo root.
  for (const p of [path.join(__dirname, '..', 'build-info.json'),
                   path.join(REPO_ROOT, 'build-info.json')]) {
    try {
      const bi = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (bi && (bi.sha || bi.committed_at)) return bi;
    } catch (e) { /* absent or unreadable — try the next location */ }
  }
  return null;
}

// Resolve the HEAD commit by reading .git plumbing directly — no git binary
// needed. Handles the normal `.git` dir, a `.git` *file* (worktree/submodule
// pointer: "gitdir: <path>"), a symbolic HEAD (ref: refs/heads/<branch>) via a
// loose ref file, and the packed-refs fallback. Returns a full 40-char SHA or
// null. Reads only ref plumbing — never object contents — so it stays cheap and
// carries no secrets (§F-15).
function readGitHead(root) {
  try {
    let gitDir = path.join(root || REPO_ROOT, '.git');
    const st = fs.statSync(gitDir);
    if (st.isFile()) {
      const m = fs.readFileSync(gitDir, 'utf8').match(/^gitdir:\s*(.+)\s*$/m);
      if (!m) return null;
      gitDir = path.resolve(root || REPO_ROOT, m[1].trim());
    }
    const head = fs.readFileSync(path.join(gitDir, 'HEAD'), 'utf8').trim();
    const ref = head.match(/^ref:\s*(.+)$/);
    if (!ref) {
      // Detached HEAD — the file already holds the raw SHA.
      return /^[0-9a-f]{40}$/i.test(head) ? head : null;
    }
    const refName = ref[1].trim();
    // Prefer a loose ref file.
    try {
      const loose = fs.readFileSync(path.join(gitDir, refName), 'utf8').trim();
      if (/^[0-9a-f]{40}$/i.test(loose)) return loose;
    } catch (e) { /* not loose — fall through to packed-refs */ }
    // Packed-refs fallback: lines are "<sha> <refname>".
    try {
      const packed = fs.readFileSync(path.join(gitDir, 'packed-refs'), 'utf8');
      for (const line of packed.split('\n')) {
        if (!line || line[0] === '#' || line[0] === '^') continue;
        const sp = line.indexOf(' ');
        if (sp > 0 && line.slice(sp + 1).trim() === refName) {
          const sha = line.slice(0, sp).trim();
          if (/^[0-9a-f]{40}$/i.test(sha)) return sha;
        }
      }
    } catch (e) { /* no packed-refs either — give up honestly */ }
    return null;
  } catch (e) {
    return null; // no .git at all — fall through to 'unknown'
  }
}

/**
 * A content fingerprint of the app's own source — the answer to "did my
 * deploy actually land?" when NO git metadata survives.
 *
 * All five sha strategies above can miss at once: a bundled deploy ships no
 * BUILD_SHA, no build-info.json, no git binary and no .git directory, and
 * then `sha` is honestly 'unknown'. That is truthful and unhelpful — for a
 * whole day it was impossible to tell which build production was serving,
 * so every "is the fix live?" question had to be answered by probing
 * behaviour.
 *
 * This does not pretend to be a commit id. It is a hash over the source that
 * actually shipped, so it CHANGES when the code changes and stays identical
 * when it does not — which is exactly the question being asked. It can also
 * be computed from a checkout, so an expected value can be stated in advance.
 *
 * §F-15: hashes only .js source under app/. Never .env, never data/, never
 * anything holding a credential — and a hash of source reveals nothing
 * regardless. Fail-open: any error yields null and the field is omitted.
 */
const FINGERPRINT_DIRS = ['lib', 'routes'];
const FINGERPRINT_FILES = ['server.js', 'auth.js', 'db.js'];

/** Hash a set of files into "<12 hex>+<count>", or null if none were readable. */
function digestOf(parts) {
  // Sorted by RELATIVE path so the digest is independent of readdir order
  // and of where the app is installed.
  parts.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));

  const h = crypto.createHash('sha256');
  let counted = 0;
  for (const [rel, abs] of parts) {
    let buf;
    try { buf = fs.readFileSync(abs); } catch (e) { continue; }
    h.update(rel);
    h.update('\0');
    h.update(buf);
    counted += 1;
  }
  if (!counted) return null;
  return `${h.digest('hex').slice(0, 12)}+${counted}`;
}

/** Collect `<dir>/*<ext>` as [relPath, absPath] pairs. Missing dir → nothing. */
function collect(root, relDir, ext, out) {
  const dir = relDir ? path.join(root, relDir) : root;
  let names;
  try { names = fs.readdirSync(dir); } catch (e) { return out; }
  for (const n of names.sort()) {
    if (!n.endsWith(ext)) continue;
    const rel = relDir ? `${relDir}/${n}` : n;
    out.push([rel, path.join(dir, n)]);
  }
  return out;
}

function fingerprint() {
  try {
    const appRoot = path.join(__dirname, '..');
    const parts = [];
    for (const f of FINGERPRINT_FILES) parts.push([f, path.join(appRoot, f)]);
    for (const d of FINGERPRINT_DIRS) collect(appRoot, d, '.js', parts);
    return digestOf(parts);
  } catch (e) {
    return null;
  }
}

/**
 * The same question, asked of the code that runs in the BROWSER.
 *
 * `build` above hashes server-side .js only. That is the right scope for what
 * it answers, and it left a hole big enough to ship a whole fix through: the
 * SSE reconnect (#973) lives entirely in public/js, so /api/version reported
 * an unchanged `build` before and after that deploy. The one instrument we
 * have for "did it land?" was blind to it, and the only way to check was
 * view-source on a script tag.
 *
 * Kept SEPARATE rather than folded into `build`, because server and client
 * versions genuinely diverge: the browser caches assets independently, so
 * "server updated, tab still on the old bundle" is a real and common state
 * and one number could not express it. Two values distinguish
 * "nothing deployed" from "deployed but the browser has not caught up".
 *
 * Covers public/js/*.js, public/*.html and styles.css — the script tags carry
 * the cache-buster versions, so an asset change that forgot to bump one still
 * moves this digest and is still visible.
 *
 * §F-15: everything hashed here is served publicly by definition. No .env, no
 * data/, and a hash of public files reveals nothing regardless.
 */
function digestTree(pub) {
  try {
    const parts = [];
    collect(pub, 'js', '.js', parts);
    collect(pub, '', '.html', parts);
    collect(pub, '', '.css', parts);
    return digestOf(parts);
  } catch (e) {
    return null;
  }
}

function assetFingerprint() {
  return digestTree(path.join(__dirname, '..', 'public'));
}

function compute() {
  let sha = (process.env.BUILD_SHA || process.env.SOURCE_COMMIT || '').trim() || null;
  let committedAt = (process.env.BUILD_TIME || '').trim() || null;

  const bi = readBuildFile();
  if (bi) {
    if (bi.sha) sha = String(bi.sha);
    if (bi.committed_at) committedAt = String(bi.committed_at);
  }

  if (!sha) sha = gitOut(['rev-parse', '--short', 'HEAD']);
  if (!committedAt) committedAt = gitOut(['show', '-s', '--format=%cI', 'HEAD']);

  // No git binary but a real .git checkout (the re-clone deploy): read the
  // ref plumbing directly rather than surrendering to 'unknown'.
  if (!sha) sha = readGitHead();

  return {
    sha: (sha ? String(sha).slice(0, 12) : 'unknown'),
    committed_at: committedAt || null,
    started_at: new Date().toISOString(),
    build: fingerprint(),
    assets: assetFingerprint(),
  };
}

const BUILD = compute();

/** Immutable build facts plus the live uptime at call time. */
function buildInfo() {
  const out = {
    sha: BUILD.sha,
    committed_at: BUILD.committed_at,
    started_at: BUILD.started_at,
    uptime_s: Math.floor(process.uptime()),
  };
  // Omitted rather than sent as null — an absent field reads as "not
  // available here", where a null invites being mistaken for a value.
  if (BUILD.build) out.build = BUILD.build;
  // Same omit-rather-than-null rule: an absent field reads as "not available
  // here", where a null invites being mistaken for a value.
  if (BUILD.assets) out.assets = BUILD.assets;
  return out;
}

module.exports = {
  buildInfo, readGitHead, fingerprint, assetFingerprint,
  // Test seam: the same algorithm against an arbitrary tree, so the
  // change-detection property can be exercised instead of asserted.
  __digestTree: digestTree,
};
