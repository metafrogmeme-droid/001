'use strict';
/**
 * The engine card names the source it could not read.
 *
 * The bot sent one flag, `record_unreadable`, for two sources: the closed-trade
 * record, and the open book (a position fetch that failed, or a position the
 * venue did not mark). The card had one sentence for the flag, "The
 * closed-trade record could not be read", so a record that had read fine was
 * blamed for a mark the venue omitted. The payload carries `open_book_unread`
 * now, and each flag has its own sentence.
 */
const test = require('node:test');
const assert = require('node:assert/strict');

const { engineCardCells } = require('../public/js/engine-card-model.js');

const RECORD = 'The closed-trade record could not be read';
const BOOK_HIDES_NET = 'The open book could not be fully read, and net P&L includes it, so it is not shown.';
const BOOK_NET_IS_CLOSED = 'The open book could not be read, so this net P&L counts closed trades only.';

test('an unread open book does not blame the closed-trade record', () => {
  const c = engineCardCells({ equity: 900, net_pnl: null, win_rate: 55,
    total_trades: 20, open_count: 2, record_unreadable: false, open_book_unread: true });
  assert.equal(c.note, BOOK_HIDES_NET);
  assert.ok(!c.note.includes(RECORD), c.note);
  assert.equal(c.netPnl.text, '—');
  assert.equal(c.winRate.text, '55.0%', 'the closed record read fine, so its rate stands');
});

test('a net P&L published over an unread book says it is the closed record only', () => {
  const c = engineCardCells({ equity: null, net_pnl: 42.5, win_rate: 60,
    total_trades: 10, open_count: null, record_unreadable: false, open_book_unread: true });
  assert.equal(c.note, BOOK_NET_IS_CLOSED);
  assert.equal(c.netPnl.text, '+42.50');
});

test('an unreadable closed record still says so, and wins over the book', () => {
  const c = engineCardCells({ equity: 900, net_pnl: null, win_rate: null,
    total_trades: 0, open_count: null, record_unreadable: true, open_book_unread: true });
  assert.ok(c.note.startsWith(RECORD), c.note);
});

test('a book that read says nothing about the book', () => {
  const c = engineCardCells({ equity: 900, net_pnl: 12, win_rate: 50,
    total_trades: 4, open_count: 1, record_unreadable: false, open_book_unread: false });
  assert.ok(!c.note.includes('open book'), c.note);
});

test('an older bot that sends no book flag is read as before', () => {
  const c = engineCardCells({ equity: 900, net_pnl: 12, win_rate: 50,
    total_trades: 4, open_count: 1 });
  assert.equal(c.note, '');
});

test('only a literal true is an unread book', () => {
  for (const v of ['true', 1, {}]) {
    const c = engineCardCells({ equity: 900, net_pnl: null, win_rate: 50,
      total_trades: 4, open_count: 1, open_book_unread: v });
    assert.ok(!c.note.includes('open book'), `${JSON.stringify(v)}: ${c.note}`);
  }
});
