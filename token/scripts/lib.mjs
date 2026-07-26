// Shared helpers for the $RCLAW devnet token tooling. DRAFT / DEVNET-ONLY.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Connection, Keypair, clusterApiUrl } from '@solana/web3.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(__dirname, '..');

export function loadConfig() {
  const raw = fs.readFileSync(path.join(ROOT, 'config', 'token.config.json'), 'utf8');
  return JSON.parse(raw);
}

// Minimal .env reader so the tooling has zero runtime dependencies beyond @solana/*.
export function loadEnv() {
  const envPath = path.join(ROOT, '.env');
  const env = { ...process.env };
  if (fs.existsSync(envPath)) {
    for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m && env[m[1]] === undefined) env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  }
  return env;
}

// Genesis hashes identify a cluster authoritatively. A URL cannot: the previous
// `url.includes('mainnet')` test passed `MAINNET` (case), every rebranded
// provider endpoint (rpc.helius.xyz, *.quiknode.pro), and any private validator.
export const MAINNET_GENESIS = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d';
export const DEVNET_GENESIS = 'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG';
export const TESTNET_GENESIS = '4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY';

/** Cluster name (as written in token.config.json) -> its genesis hash. */
export const CLUSTER_GENESIS = {
  devnet: DEVNET_GENESIS,
  testnet: TESTNET_GENESIS,
  'mainnet-beta': MAINNET_GENESIS,
  mainnet: MAINNET_GENESIS,
};

/**
 * Open a connection and prove which chain is on the other end.
 *
 * `expectCluster` makes the `cluster` field in token.config.json load-bearing
 * instead of decorative — previously nothing read it, so the field a reviewer
 * most naturally trusts had no effect on anything.
 *
 * Fails CLOSED: an unrecognised genesis hash throws rather than being waved
 * through, so a new provider or a private validator cannot silently become the
 * cluster this draft tooling signs against.
 */
export async function getConnection(env, { expectCluster = 'devnet' } = {}) {
  const url = env.RPC_URL || clusterApiUrl('devnet');

  if (!Object.prototype.hasOwnProperty.call(CLUSTER_GENESIS, expectCluster)) {
    throw new Error(
      `Unknown cluster ${JSON.stringify(expectCluster)} in config. ` +
        `Expected one of: ${Object.keys(CLUSTER_GENESIS).join(', ')}.`
    );
  }
  const expected = CLUSTER_GENESIS[expectCluster];
  if (expected === MAINNET_GENESIS) {
    throw new Error(
      `Config declares cluster "${expectCluster}". This is draft/devnet tooling — mainnet ` +
        'is gated behind legal review + audit (see docs/TOKEN_ROADMAP.md §10-11).'
    );
  }

  const conn = new Connection(url, 'confirmed');
  const genesis = await conn.getGenesisHash();
  if (genesis === MAINNET_GENESIS) {
    throw new Error(
      `Refusing to run against mainnet-beta (genesis ${genesis}). This is draft/devnet ` +
        'tooling — mainnet is gated behind legal review + audit (see docs/TOKEN_ROADMAP.md §10-11).'
    );
  }
  if (genesis !== expected) {
    throw new Error(
      `Cluster mismatch: config says "${expectCluster}" (genesis ${expected}) but ${url} ` +
        `reports genesis ${genesis}. Refusing to continue.`
    );
  }
  return conn;
}

/**
 * Reject a key file any other account on the box can read.
 *
 * This one file holds mint, metadata, presale and LP authority plus the entire
 * supply, and KEYPAIR_PATH is operator-supplied, so the check belongs at the
 * load site rather than only where the key is generated.
 */
export function assertKeyfilePermissions(abs) {
  if (process.platform === 'win32') return; // POSIX mode bits are meaningless here
  const mode = fs.statSync(abs).mode & 0o777;
  if (mode & 0o077) {
    throw new Error(
      `Keypair ${abs} is group/world-readable (mode ${mode.toString(8)}). ` +
        'Run `chmod 600` on it before using it.'
    );
  }
}

export function loadKeypair(env) {
  const kpPath = env.KEYPAIR_PATH || './.keys/mint-payer.json';
  const abs = path.isAbsolute(kpPath) ? kpPath : path.join(ROOT, kpPath);
  if (!fs.existsSync(abs)) {
    throw new Error(`Keypair not found at ${abs}. Run \`npm run keygen\` or set KEYPAIR_PATH.`);
  }
  assertKeyfilePermissions(abs);
  const secret = Uint8Array.from(JSON.parse(fs.readFileSync(abs, 'utf8')));
  return Keypair.fromSecretKey(secret);
}

export function saveArtifact(name, data) {
  const dir = path.join(ROOT, '.artifacts');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, JSON.stringify(data, null, 2));
  return file;
}

export function readArtifact(name) {
  const file = path.join(ROOT, '.artifacts', name);
  if (!fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}
