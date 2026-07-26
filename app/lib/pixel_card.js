'use strict';
/**
 * Pixel cards — PNG rendering from the standard library alone.
 *
 * Farcaster frames and OG previews need real PNGs, and this repo ships no
 * image dependency. So: a minimal PNG encoder (node's built-in zlib +
 * hand-rolled CRC32) and a 5×7 bitmap font, drawing retro-terminal receipt
 * cards. Deterministic: same inputs, same bytes.
 *
 * The font is uppercase-only by design; unknown characters render as space
 * rather than guessing at glyphs.
 */

const zlib = require('zlib');

// ── CRC32 (PNG chunk checksums) ──────────────────────────────────────────────
const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function chunk(type, data) {
  const out = Buffer.alloc(8 + data.length + 4);
  out.writeUInt32BE(data.length, 0);
  out.write(type, 4, 'ascii');
  data.copy(out, 8);
  out.writeUInt32BE(crc32(Buffer.concat([Buffer.from(type, 'ascii'), data])), 8 + data.length);
  return out;
}

/** RGBA canvas → PNG buffer. */
function encodePng(width, height, rgba) {
  const sig = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 6;   // color type: RGBA
  // compression 0, filter 0, interlace 0 — already zeroed
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (width * 4 + 1)] = 0; // filter: none
    rgba.copy(raw, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  }
  return Buffer.concat([
    sig,
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// ── 5×7 font (uppercase, digits, punctuation the cards need) ────────────────
const F = {
  A: ['.XXX.', 'X...X', 'X...X', 'XXXXX', 'X...X', 'X...X', 'X...X'],
  B: ['XXXX.', 'X...X', 'XXXX.', 'X...X', 'X...X', 'X...X', 'XXXX.'],
  C: ['.XXXX', 'X....', 'X....', 'X....', 'X....', 'X....', '.XXXX'],
  D: ['XXXX.', 'X...X', 'X...X', 'X...X', 'X...X', 'X...X', 'XXXX.'],
  E: ['XXXXX', 'X....', 'XXXX.', 'X....', 'X....', 'X....', 'XXXXX'],
  F: ['XXXXX', 'X....', 'XXXX.', 'X....', 'X....', 'X....', 'X....'],
  G: ['.XXXX', 'X....', 'X....', 'X..XX', 'X...X', 'X...X', '.XXX.'],
  H: ['X...X', 'X...X', 'XXXXX', 'X...X', 'X...X', 'X...X', 'X...X'],
  I: ['XXXXX', '..X..', '..X..', '..X..', '..X..', '..X..', 'XXXXX'],
  J: ['XXXXX', '...X.', '...X.', '...X.', '...X.', 'X..X.', '.XX..'],
  K: ['X...X', 'X..X.', 'XXX..', 'X.X..', 'X..X.', 'X...X', 'X...X'],
  L: ['X....', 'X....', 'X....', 'X....', 'X....', 'X....', 'XXXXX'],
  M: ['X...X', 'XX.XX', 'X.X.X', 'X.X.X', 'X...X', 'X...X', 'X...X'],
  N: ['X...X', 'XX..X', 'X.X.X', 'X..XX', 'X...X', 'X...X', 'X...X'],
  O: ['.XXX.', 'X...X', 'X...X', 'X...X', 'X...X', 'X...X', '.XXX.'],
  P: ['XXXX.', 'X...X', 'X...X', 'XXXX.', 'X....', 'X....', 'X....'],
  Q: ['.XXX.', 'X...X', 'X...X', 'X...X', 'X.X.X', 'X..X.', '.XX.X'],
  R: ['XXXX.', 'X...X', 'X...X', 'XXXX.', 'X.X..', 'X..X.', 'X...X'],
  S: ['.XXXX', 'X....', 'X....', '.XXX.', '....X', '....X', 'XXXX.'],
  T: ['XXXXX', '..X..', '..X..', '..X..', '..X..', '..X..', '..X..'],
  U: ['X...X', 'X...X', 'X...X', 'X...X', 'X...X', 'X...X', '.XXX.'],
  V: ['X...X', 'X...X', 'X...X', 'X...X', 'X...X', '.X.X.', '..X..'],
  W: ['X...X', 'X...X', 'X...X', 'X.X.X', 'X.X.X', 'XX.XX', 'X...X'],
  X: ['X...X', 'X...X', '.X.X.', '..X..', '.X.X.', 'X...X', 'X...X'],
  Y: ['X...X', 'X...X', '.X.X.', '..X..', '..X..', '..X..', '..X..'],
  Z: ['XXXXX', '....X', '...X.', '..X..', '.X...', 'X....', 'XXXXX'],
  0: ['.XXX.', 'X...X', 'X..XX', 'X.X.X', 'XX..X', 'X...X', '.XXX.'],
  1: ['..X..', '.XX..', '..X..', '..X..', '..X..', '..X..', 'XXXXX'],
  2: ['.XXX.', 'X...X', '....X', '...X.', '..X..', '.X...', 'XXXXX'],
  3: ['XXXX.', '....X', '....X', '.XXX.', '....X', '....X', 'XXXX.'],
  4: ['X...X', 'X...X', 'X...X', 'XXXXX', '....X', '....X', '....X'],
  5: ['XXXXX', 'X....', 'XXXX.', '....X', '....X', 'X...X', '.XXX.'],
  6: ['.XXX.', 'X....', 'XXXX.', 'X...X', 'X...X', 'X...X', '.XXX.'],
  7: ['XXXXX', '....X', '...X.', '..X..', '.X...', '.X...', '.X...'],
  8: ['.XXX.', 'X...X', 'X...X', '.XXX.', 'X...X', 'X...X', '.XXX.'],
  9: ['.XXX.', 'X...X', 'X...X', '.XXXX', '....X', '....X', '.XXX.'],
  ':': ['.....', '..X..', '.....', '.....', '.....', '..X..', '.....'],
  '.': ['.....', '.....', '.....', '.....', '.....', '.XX..', '.XX..'],
  '-': ['.....', '.....', '.....', 'XXXXX', '.....', '.....', '.....'],
  '/': ['....X', '....X', '...X.', '..X..', '.X...', 'X....', 'X....'],
  '>': ['X....', '.X...', '..X..', '...X.', '..X..', '.X...', 'X....'],
  '#': ['.X.X.', 'XXXXX', '.X.X.', '.X.X.', '.X.X.', 'XXXXX', '.X.X.'],
  ' ': ['.....', '.....', '.....', '.....', '.....', '.....', '.....'],
};

class Card {
  constructor(width, height, bg) {
    this.w = width;
    this.h = height;
    this.px = Buffer.alloc(width * height * 4);
    this.fillRect(0, 0, width, height, bg || [10, 11, 16, 255]);
  }

  fillRect(x, y, w, h, c) {
    for (let j = Math.max(0, y); j < Math.min(this.h, y + h); j++) {
      for (let i = Math.max(0, x); i < Math.min(this.w, x + w); i++) {
        const o = (j * this.w + i) * 4;
        this.px[o] = c[0]; this.px[o + 1] = c[1]; this.px[o + 2] = c[2]; this.px[o + 3] = c[3];
      }
    }
  }

  /** Draw uppercase text at (x, y), pixel scale s. Unknown chars → space. */
  text(str, x, y, s, c) {
    let cx = x;
    for (const raw of String(str).toUpperCase()) {
      const g = F[raw] || F[' '];
      for (let row = 0; row < 7; row++) {
        for (let col = 0; col < 5; col++) {
          if (g[row][col] === 'X') this.fillRect(cx + col * s, y + row * s, s, s, c);
        }
      }
      cx += 6 * s; // 5px glyph + 1px gap
    }
    return this;
  }

  /** Width in pixels of `str` at scale s (for centering). */
  static textWidth(str, s) { return String(str).length * 6 * s - s; }

  center(str, y, s, c) {
    return this.text(str, Math.max(0, Math.round((this.w - Card.textWidth(str, s)) / 2)), y, s, c);
  }

  png() { return encodePng(this.w, this.h, this.px); }
}

const COLORS = {
  bg: [10, 11, 16, 255],
  gold: [230, 195, 75, 255],
  text: [220, 222, 228, 255],
  muted: [110, 114, 128, 255],
  up: [47, 191, 113, 255],
  down: [255, 92, 108, 255],
};

module.exports = { Card, encodePng, crc32, COLORS, FONT: F };
