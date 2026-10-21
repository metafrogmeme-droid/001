#!/usr/bin/env node
/**
 * Compile MemeclawFactory.sol and write compiled/MemeclawFactory.json: the
 * ABI and bytecode of the factory and the coin, with the compiler and
 * settings that made them.
 *
 * The artifact is committed because the website reads it: the operator's
 * deploy page sends this bytecode from the operator's wallet, and the server
 * checks that the code at a factory address is this runtime code before the
 * page will point anyone at it. A build that does not reproduce the
 * committed artifact fails (--check, and the test that calls build()).
 *
 *   node scripts/build.mjs           # write compiled/MemeclawFactory.json
 *   node scripts/build.mjs --check   # exit 1 if the committed file differs
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createHash } from 'node:crypto';
import solc from 'solc';

const here = dirname(fileURLToPath(import.meta.url));
export const ROOT = join(here, '..');
export const SOURCE_FILE = 'MemeclawFactory.sol';
export const ARTIFACT = join(ROOT, 'compiled', 'MemeclawFactory.json');
export const SETTINGS = {
  optimizer: { enabled: true, runs: 200 },
  // No PUSH0: paris bytecode runs on every EVM chain, whatever fork it is on.
  evmVersion: 'paris',
  outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object'] } },
};

/** The artifact as an object, compiled from the source on disk. */
export function build() {
  const source = readFileSync(join(ROOT, SOURCE_FILE), 'utf8');
  const input = { language: 'Solidity', sources: { [SOURCE_FILE]: { content: source } }, settings: SETTINGS };
  const out = JSON.parse(solc.compile(JSON.stringify(input)));
  const fatal = (out.errors || []).filter((e) => e.severity === 'error');
  if (fatal.length) throw new Error('compile errors: ' + JSON.stringify(fatal, null, 2));
  const pick = (name) => {
    const c = out.contracts[SOURCE_FILE][name];
    return {
      abi: c.abi,
      bytecode: '0x' + c.evm.bytecode.object,
      deployedBytecode: '0x' + c.evm.deployedBytecode.object,
    };
  };
  return {
    _generated: 'contracts/memeclaw/scripts/build.mjs from contracts/memeclaw/MemeclawFactory.sol. Do not edit by hand.',
    compiler: solc.version(),
    settings: { optimizer: SETTINGS.optimizer, evmVersion: SETTINGS.evmVersion },
    source_sha256: createHash('sha256').update(source).digest('hex'),
    contracts: { MemeclawFactory: pick('MemeclawFactory'), MemeclawToken: pick('MemeclawToken') },
  };
}

export function serialize(artifact) {
  return JSON.stringify(artifact, null, 1) + '\n';
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const text = serialize(build());
  if (process.argv.includes('--check')) {
    const now = existsSync(ARTIFACT) ? readFileSync(ARTIFACT, 'utf8') : null;
    if (now !== text) {
      process.stderr.write('compiled/MemeclawFactory.json is not the built artifact: run npm run build\n');
      process.exit(1);
    }
  } else {
    mkdirSync(dirname(ARTIFACT), { recursive: true });
    writeFileSync(ARTIFACT, text);
    process.stdout.write(`wrote ${ARTIFACT}\n`);
  }
}
