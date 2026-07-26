'use strict';
// The ERC-8257 manifest is a PUBLIC, hashed, on-chain-referenced artifact. Its
// prose is not decoration — an agent reads it to decide whether to trust the
// endpoint, and the operator registers keccak256(canonical bytes) against it.
//
// It said "Every tool serves data the public site already publishes" for the
// whole of its life. That was true until the four Guardian tools shipped, and
// then it was quietly false: they answer from input the CALLER sends, not from
// anything this site publishes. Nothing failed, because the manifest derives
// its tool list from the registry and no test asserted the prose still matched
// the registry it advertises.
//
// These tests pin the property that went stale, and pin the machine-readable
// split so a future family cannot over-claim in silence.

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const t8257 = require('../lib/tool8257');
const { TOOLS } = require('../routes/mcp');

const names = () => Object.keys(TOOLS);
const computed = () => names().filter((n) => TOOLS[n].computesOnInput);
const published = () => names().filter((n) => !TOOLS[n].computesOnInput);

test('both tool families are actually registered', () => {
  // If this ever goes to zero the tests below stop meaning anything.
  assert.ok(published().length > 0, 'no published-data tools registered');
  assert.ok(computed().length > 0, 'no caller-input tools registered');
});

test('the manifest no longer claims every tool serves published data', () => {
  const m = t8257.buildManifest({ tools: TOOLS });
  assert.doesNotMatch(
    m.description, /[Ee]very tool serves data the public site/,
    'the manifest over-claims: these evaluate caller input, not published '
      + `data — ${computed().join(', ')}`);
});

test('the manifest description names BOTH families and their limits', () => {
  const m = t8257.buildManifest({ tools: TOOLS });
  // The caller-input family is the one an agent must be warned about: it is
  // the only one where what the agent sends is what gets judged.
  assert.match(m.description, /input the caller supplies/i,
    'the description never says some tools judge what the caller sends');
  assert.match(m.description, /storing nothing/i, 'retention is not stated');
  assert.match(m.description, /never a verdict/i,
    'a safety tool advertised without "never a verdict" reads as authoritative');
  // The §4 line that must survive any rewrite of this prose.
  assert.match(m.description, /no tool sees an account, places an order or moves funds/i);
});

test('toolFamilies is derived from the registry, not hand-maintained', () => {
  const m = t8257.buildManifest({ tools: TOOLS });
  assert.deepEqual(m.toolFamilies.callerInput, computed().sort());
  assert.deepEqual(m.toolFamilies.publishedData, published().sort());
  // Every registered tool is classified — no tool can be advertised in the
  // enum while sitting in neither family.
  const both = [...m.toolFamilies.publishedData, ...m.toolFamilies.callerInput].sort();
  assert.deepEqual(both, names().sort());
  assert.deepEqual(m.inputs.properties.tool.enum.slice().sort(), both);
});

test('a new caller-input tool moves families without touching the manifest', () => {
  // The whole point of deriving it: adding one here must not require anyone to
  // remember to edit prose or a list.
  const fake = {
    ...TOOLS,
    judge_my_thing: { computesOnInput: true, description: 'x', inputSchema: {}, handler: async () => ({}) },
  };
  const m = t8257.buildManifest({ tools: fake });
  assert.ok(m.toolFamilies.callerInput.includes('judge_my_thing'));
  assert.ok(!m.toolFamilies.publishedData.includes('judge_my_thing'));
});

test('the five Guardian tools are the caller-input family', () => {
  assert.deepEqual(
    computed().sort(),
    ['compile_intent', 'plan_escape', 'scan_transaction', 'stress_portfolio',
      'xray_transaction'],
    'a Guardian tool shipped without computesOnInput — the manifest would then '
      + 'advertise it as serving data the public site publishes, which is false');
});

test('changing the tool set changes the hash — the operator must re-register', () => {
  // Not a complaint about the design (one source of truth is correct), but the
  // consequence is load-bearing and worth a failing test if it ever stops
  // holding: a registered hash silently stops verifying when tools change.
  const a = t8257.manifestHash(t8257.buildManifest({ tools: TOOLS }));
  const fewer = { ...TOOLS };
  delete fewer.scan_transaction;
  const b = t8257.manifestHash(t8257.buildManifest({ tools: fewer }));
  assert.notEqual(a, b, 'the manifest hash does not cover the advertised tool set');
  const plan = t8257.buildRegistrationPlan({ tools: TOOLS });
  assert.equal(plan.manifest_hash, a, 'the plan must hash the manifest actually served');
  assert.match(JSON.stringify(plan), /byte-identical/,
    'the plan must still warn that the served manifest has to stay byte-identical');
});
