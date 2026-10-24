/**
 * Hyperliquid testnet userFills watcher for ADL re-entry cover.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 *
 * It subscribes to the testnet websocket and reports fills whose `dir` is
 * exactly "Auto-Deleveraging". It does not copy a price, it does not sign,
 * and it does not send a transaction. A snapshot is history arriving on
 * connect, so those fills are not reported again. A message that cannot be
 * read is reported as unreadable, not as an empty book.
 */

import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const flags = require('../flags.cjs') as {
  flagOn: (name: string, env: Record<string, string | undefined> | null | undefined) => boolean;
  ADL_FLAG: string;
};

export const TESTNET_WS = 'wss://api.hyperliquid-testnet.xyz/ws';
export const ADL_DIR = 'Auto-Deleveraging';

export type SocketLike = {
  send: (data: string) => void;
  close: () => void;
  onmessage: ((raw: string) => void) | null;
};

export type AdlNotice = {
  user: string;
  coin: string | null;
  time: number | null;
};

export type FillMessage =
  | { ok: false; reason: 'unreadable' }
  | { ok: true; snapshot: boolean; fills: Array<{ dir: string; coin: string | null; time: number | null }> };

export function assertTestnetUrl(url: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error('refused');
  }
  if (parsed.protocol !== 'wss:') throw new Error('refused');
  if (parsed.hostname !== 'api.hyperliquid-testnet.xyz') throw new Error('refused');
  if (parsed.port !== '' && parsed.port !== '443') throw new Error('refused');
  if (parsed.pathname !== '/ws') throw new Error('refused');
  if (parsed.username !== '' || parsed.password !== '') throw new Error('refused');
  if (parsed.search !== '' || parsed.hash !== '') throw new Error('refused');
  return parsed;
}

export function userAddress(user: string): string {
  if (!/^0x[0-9a-fA-F]{40}$/.test(user)) throw new Error('refused');
  return user;
}

export function readMessage(raw: string): FillMessage {
  let msg: unknown;
  try {
    msg = JSON.parse(raw);
  } catch {
    return { ok: false, reason: 'unreadable' };
  }
  if (msg === null || typeof msg !== 'object') return { ok: false, reason: 'unreadable' };
  const data = (msg as { data?: unknown }).data;
  if (data === null || typeof data !== 'object') return { ok: false, reason: 'unreadable' };
  const snap = (data as { isSnapshot?: unknown }).isSnapshot;
  if (typeof snap !== 'boolean') return { ok: false, reason: 'unreadable' };
  const rows = (data as { fills?: unknown }).fills;
  if (!Array.isArray(rows)) return { ok: false, reason: 'unreadable' };
  const fills = [];
  for (const row of rows) {
    if (row === null || typeof row !== 'object') return { ok: false, reason: 'unreadable' };
    const dir = (row as { dir?: unknown }).dir;
    if (typeof dir !== 'string') return { ok: false, reason: 'unreadable' };
    const coin = (row as { coin?: unknown }).coin;
    const time = (row as { time?: unknown }).time;
    fills.push({
      dir,
      coin: typeof coin === 'string' ? coin : null,
      time: typeof time === 'number' ? time : null,
    });
  }
  return { ok: true, snapshot: snap, fills };
}

export type WatchHandle = { opened: boolean; stop: () => void };

export function startUserFillsWatch(opts: {
  env: Record<string, string | undefined> | null;
  url?: string;
  user: string;
  connect: (url: string) => SocketLike;
  onAdl: (notice: AdlNotice) => void;
  onUnreadable: () => void;
}): WatchHandle {
  if (!flags.flagOn(flags.ADL_FLAG, opts.env)) {
    return { opened: false, stop() {} };
  }
  const url = assertTestnetUrl(opts.url === undefined ? TESTNET_WS : opts.url);
  const user = userAddress(opts.user);
  const socket = opts.connect(url.toString());
  socket.send(JSON.stringify({
    method: 'subscribe',
    subscription: { type: 'userFills', user },
  }));
  socket.onmessage = (raw: string) => {
    const parsed = readMessage(raw);
    if (!parsed.ok) {
      opts.onUnreadable();
      return;
    }
    if (parsed.snapshot) return;
    for (const fill of parsed.fills) {
      if (fill.dir !== ADL_DIR) continue;
      opts.onAdl({ user, coin: fill.coin, time: fill.time });
    }
  };
  return {
    opened: true,
    stop() {
      socket.close();
    },
  };
}
