'use strict';
// The caption under a chat answer is the server's sentence when it sent one,
// and the same sentence built from the tool list when it did not.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const caption = require('../public/js/chat-caption.js');

test('the server sentence wins, and a failed tool is marked', () => {
  assert.equal(caption.readFromCaption({
    read_from: 'read: get_portfolio, whynot\u2717',
    tools: [{ name: 'other', ok: true }],
  }), 'read: get_portfolio, whynot\u2717');
  assert.equal(caption.readFromCaption({
    tools: [{ name: 'get_portfolio', ok: true }, { name: 'whynot', ok: false }],
  }), 'read: get_portfolio, whynot\u2717');
  assert.equal(caption.readFromCaption({}), '');
  assert.equal(caption.readFromCaption(null), '');
});

test('both pages load the caption before chat.js', () => {
  for (const page of ['index.html', 'dashboard.html']) {
    const html = fs.readFileSync(path.join(__dirname, '..', 'public', page), 'utf8');
    const cap = html.indexOf('/js/chat-caption.js?v=1');
    const chat = html.indexOf('/js/chat.js?v=38');
    assert.ok(cap > 0 && chat > cap, page);
  }
  const chatJs = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
  assert.ok(chatJs.includes('RCChatCaption.readFromCaption'));
});
