'use strict';

/**
 * The local hide list, read the way the bot writes it.
 *
 * A missing file is an empty list. A file that cannot be parsed is
 * unreadable, and that is not an empty list. This module does not write.
 */
const fs = require('fs');
const path = require('path');
const { REPO_ROOT, explicitStateDir } = require('./state_dir');

const OWNER = /^[A-Za-z0-9_-]{1,64}$/;
const ADDR = /^0x[0-9a-fA-F]{40}$/;

function bookDir() {
  const explicit = explicitStateDir();
  const base = explicit ? explicit : path.join(REPO_ROOT, 'data');
  return path.join(base, 'quarantine');
}

function ownerOk(owner) {
  return typeof owner === 'string' && OWNER.test(owner);
}

function contractKey(chainId, contract, tokenId) {
  if (typeof chainId === 'boolean') return null;
  const chain = Number(chainId);
  if (!Number.isInteger(chain) || chain < 0) return null;
  if (typeof contract !== 'string' || !ADDR.test(contract)) return null;
  const base = chain + ':' + contract.toLowerCase();
  const tid = tokenId == null ? '' : String(tokenId).trim();
  if (!tid) return base;
  if (tid.length > 80 || /[\n\r/:\\]/.test(tid)) return null;
  return base + ':' + tid;
}

function emptyBook() {
  return { state: 'empty', assets: [], own: [], counterparties: [], contracts: new Set() };
}

function addresses(raw) {
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const item of raw) {
    if (typeof item === 'string' && ADDR.test(item)) out.push(item.toLowerCase());
  }
  return out;
}

function loadBook(owner) {
  if (typeof owner !== 'string' || !owner) {
    const book = emptyBook();
    book.state = 'invalid';
    return book;
  }
  if (!ownerOk(owner)) {
    const book = emptyBook();
    book.state = 'unreadable';
    return book;
  }
  const file = path.join(bookDir(), owner + '.json');
  if (!fs.existsSync(file)) return emptyBook();
  let data;
  try {
    data = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    const book = emptyBook();
    book.state = 'unreadable';
    return book;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    const book = emptyBook();
    book.state = 'unreadable';
    return book;
  }
  const assets = [];
  const contracts = new Set();
  const rows = Array.isArray(data.assets) ? data.assets : [];
  for (const item of rows) {
    if (!item || typeof item !== 'object') continue;
    const key = contractKey(item.chain_id, item.contract, item.token_id || '');
    if (!key) continue;
    const tid = item.token_id == null || String(item.token_id).trim() === ''
      ? '' : String(item.token_id).trim();
    assets.push({
      chain_id: Number(item.chain_id),
      contract: String(item.contract).toLowerCase(),
      token_id: tid,
    });
    contracts.add(key);
  }
  return {
    state: 'read',
    assets: assets,
    own: addresses(data.own),
    counterparties: addresses(data.counterparties),
    contracts: contracts,
  };
}

module.exports = { bookDir, ownerOk, contractKey, loadBook, emptyBook };
