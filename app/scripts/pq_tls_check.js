#!/usr/bin/env node
'use strict';
/**
 * Does every host the deploy serves negotiate post-quantum TLS
 * (X25519MLKEM768)? One handshake per host, offering what a current browser
 * offers; the group the server chose is the answer (app/lib/pq_tls.js).
 *
 *   node app/scripts/pq_tls_check.js                 # the deploy's hosts
 *   node app/scripts/pq_tls_check.js example.com:443 # these hosts instead
 *   node app/scripts/pq_tls_check.js --json          # one JSON line
 *
 * The deploy's hosts come from the variables that already name them:
 * WEB_URL (default https://humanoid-traders.com) and its www twin,
 * MEMECLAW_HOSTS, and BOT_GATEWAY_URL when it is https.
 *
 * Exit status, as scripts/verify_deploy.sh has it:
 *   0  every host is post-quantum            a verdict
 *   1  a host negotiated a classical group   a verdict
 *   3  something could not be checked        NOT a verdict
 *
 * Run it from a machine whose traffic reaches the host directly. Through a
 * TLS-intercepting proxy it measures the proxy, and a CA bundle that the
 * check had to be given is the sign of one.
 */
const pq = require('../lib/pq_tls');

function parseTarget(arg) {
  const m = /^([a-z0-9.-]+)(?::(\d{1,5}))?$/i.exec(String(arg).replace(/^https:\/\//i, '').replace(/\/.*$/, ''));
  return m ? { host: m[1].toLowerCase(), port: m[2] ? Number(m[2]) : 443 } : null;
}

/** `check` is the one handshake per host (pq_tls.checkHost); a test hands it a CA. */
async function main(argv = process.argv.slice(2), env = process.env, log = console.log, check = pq.checkHost) {
  const json = argv.includes('--json');
  const args = argv.filter((a) => a !== '--json');
  const targets = args.length ? args.map(parseTarget) : pq.deployHosts(env);
  const bad = args.filter((a, i) => !targets[i]);
  if (bad.length) {
    log(`not a host: ${bad.join(', ')}`);
    return 3;
  }
  const results = [];
  for (const t of targets) results.push(await check(t.host, t.port));
  const code = pq.exitCode(results);
  if (json) {
    log(JSON.stringify({ group: pq.PQ_GROUP, exit: code, results }));
    return code;
  }
  for (const r of results) {
    const where = `${r.host}:${r.port}`;
    if (r.verdict === 'pq') log(`PQ        ${where}  ${r.group} (${r.protocol})`);
    else if (r.verdict === 'classical') log(`CLASSICAL ${where}  ${r.group} (${r.protocol}): not post-quantum`);
    else if (r.verdict === 'cannot_check') log(`CANNOT CHECK ${where}  ${r.reason}`);
    else log(`UNKNOWN   ${where}  ${r.reason}`);
  }
  log(code === 0 ? `every host negotiates ${pq.PQ_GROUP}`
    : code === 1 ? 'a host is not post-quantum (a verdict)'
      : 'something could not be checked (not a verdict)');
  return code;
}

if (require.main === module) {
  main().then((code) => { process.exitCode = code; }, (e) => {
    console.log(`the check could not run: ${(e && e.name) || 'Error'}`);
    process.exitCode = 3;
  });
}

module.exports = { main, parseTarget };
