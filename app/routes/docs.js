'use strict';
/**
 * The documentation, public: the docs/gitbook/ folder as the website serves it
 * at /docs (app/lib/docs_pages.js renders it).
 *
 * A folder that could not be read is a 503 naming the exception class, never
 * `{"pages": []}`: an empty list says there are no docs.
 */

const express = require('express');
const docs = require('../lib/docs_pages');

const router = express.Router();

const NOTE = 'The documentation is written in English; the page around it speaks every supported language.';

function folderUnread(res, err) {
  const name = err && err.name ? String(err.name) : 'Error';
  console.error('Docs unreadable:', name);
  return res.status(503).json({ error: 'docs_unreadable', exception: name });
}

router.get('/pages', (req, res) => {
  let all;
  try {
    all = docs.listPages();
  } catch (err) {
    return folderUnread(res, err);
  }
  res.json({ pages: all.map(({ slug, title }) => ({ slug, title })), folder: docs.FOLDER, note: NOTE });
});

router.get('/pages/:slug', (req, res) => {
  let p;
  try {
    p = docs.getPage(req.params.slug);
  } catch (err) {
    return folderUnread(res, err);
  }
  if (!p) return res.status(404).json({ error: 'no_such_page' });
  res.json({ slug: p.slug, title: p.title, html: p.html, source: p.source, note: NOTE });
});

router.get('/assets/:name', (req, res) => {
  let file;
  try {
    file = docs.assetPath(req.params.name);
  } catch (err) {
    return folderUnread(res, err);
  }
  if (!file) return res.status(404).json({ error: 'no_such_asset' });
  res.setHeader('Cache-Control', 'public, max-age=3600');
  res.sendFile(file);
});

module.exports = router;
