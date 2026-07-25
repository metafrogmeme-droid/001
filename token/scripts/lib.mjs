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

export function getConnection(env) {
  const url = env.RPC_URL || clusterApiUrl('devnet');
  if (url.includes('mainnet')) {
    throw new Error(
      'Refusing to run against mainnet. This is draft/devnet tooling — mainnet is gated behind ' +
        'legal review + audit (see docs/TOKEN_ROADMAP.md §10-11).'
    );
  }
  return new Connection(url, 'confirmed');
}

export function loadKeypair(env) {
  const kpPath = env.KEYPAIR_PATH || './.keys/mint-payer.json';
  const abs = path.isAbsolute(kpPath) ? kpPath : path.join(ROOT, kpPath);
  if (!fs.existsSync(abs)) {
    throw new Error(`Keypair not found at ${abs}. Run \`npm run keygen\` or set KEYPAIR_PATH.`);
  }
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
