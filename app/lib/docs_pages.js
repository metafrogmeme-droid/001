'use strict';
/**
 * The documentation on the website: docs/gitbook/, served at /docs.
 *
 * The folder is the one the README and the agent card link on GitHub. The
 * website reads its copy under app/content/docs/ (app/scripts/sync_content.js
 * writes it, and a test holds the copy byte-identical to the original), because
 * a deploy of app/ carries nothing beside it.
 *
 * SUMMARY.md is the table of contents, as it was for GitBook: its order is the
 * page order and its titles are the sidebar's. A page is served only if the
 * table names it. app/test/the_docs_page_serves_the_docs_folder.test.js holds
 * the table and the folder to each other, so a page cannot be in the folder and
 * unreachable, or in the table and missing.
 *
 * Rendering is ESCAPE-FIRST, like the lessons (learn_lessons.js): the whole
 * source is HTML-escaped before any markdown is read, so a doc can produce only
 * the markup below: headings, paragraphs, lists (one level of nesting, task
 * boxes), fenced code, tables, block quotes, rules, inline code, bold, italics,
 * links and images. A link is a claim a reader acts on, so it is printed only
 * when it goes somewhere this site can vouch for:
 *
 *   - https://… opens in a new tab;
 *   - another page of the docs (`faq.md`, `faq.md#part`) goes to /docs/<slug>;
 *   - an image is printed only when it is a file in the folder.
 *
 * Anything else (another scheme, a page the docs do not have) prints its text
 * with no link: an href that leads nowhere is worse than none.
 */

const fs = require('fs');
const path = require('path');

const DOCS_DIR = path.join(__dirname, '..', 'content', 'docs');
const SUMMARY = 'SUMMARY.md';
const SOURCE = 'https://github.com/metafrogmeme-droid/001/blob/main/docs/gitbook/';
const FOLDER = 'https://github.com/metafrogmeme-droid/001/tree/main/docs/gitbook';
const PAGE_FILE = /^[a-z0-9][a-z0-9-]*\.md$|^README\.md$/;
const ASSET_FILE = /^[a-z0-9][a-z0-9-]*\.png$/;
const SLUG = /^[a-z0-9][a-z0-9-]*$/;
const FRAGMENT = /^[a-z0-9][a-z0-9-]*$/;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

/** README.md is the front page, as GitHub shows the folder; every other page is its name. */
function slugOf(file) {
  return file === 'README.md' ? 'introduction' : file.replace(/\.md$/, '');
}

/**
 * The table of contents: `* [Title](file.md)` lines, in order. A table that
 * names no page, a page twice, or a file outside the folder's own names THROWS:
 * a table that cannot be read is not a site with no docs.
 */
function parseSummary(src) {
  const pages = [];
  for (const line of String(src).split('\n')) {
    const m = /^\s*[*-]\s+\[([^\]]+)\]\(([^)\s]+)\)\s*$/.exec(line);
    if (!m) continue;
    const file = m[2];
    if (!PAGE_FILE.test(file)) throw new Error(`SUMMARY.md names ${JSON.stringify(file)}, not a page in the folder`);
    pages.push({ slug: slugOf(file), file, title: m[1].trim() });
  }
  if (!pages.length) throw new Error('SUMMARY.md names no page');
  const slugs = new Set(pages.map((p) => p.slug));
  if (slugs.size !== pages.length) throw new Error('SUMMARY.md names a page twice');
  return pages;
}

// ── inline ────────────────────────────────────────────────────────────────

/** The href a markdown link may carry, or null when it would lead nowhere this site vouches for. */
function linkHref(url, ctx) {
  if (/^https:\/\/[^\s]+$/.test(url)) return { href: url, external: true };
  const m = /^(?:\.\/)?([^/#]+\.md)(?:#([^#]*))?$/.exec(url);
  if (m) {
    const slug = ctx.pagesByFile.get(m[1]);
    if (!slug) return null;
    const frag = m[2] && FRAGMENT.test(m[2]) ? '#' + m[2] : '';
    return { href: '/docs/' + slug + frag, external: false };
  }
  const f = /^#([^#]+)$/.exec(url);
  if (f && FRAGMENT.test(f[1])) return { href: url, external: false };
  return null;
}

function emphasis(s) {
  return s
    .replace(/\*\*(?=\S)([^*]+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*(?=\S)([^*]+?)\*(?![*\w])/g, '$1<em>$2</em>');
}

/**
 * Inline markdown over ESCAPED text. Code spans are cut out first and their
 * contents are never read again; links and images become placeholders before
 * emphasis runs, so a `*` in a URL cannot become an <em>.
 */
function inline(text, ctx) {
  const held = [];
  const hold = (html) => { held.push(html); return '\u0000' + (held.length - 1) + '\u0000'; };
  let s = String(text).replace(/(`+)(.+?)\1(?!`)/g, (_, _ticks, code) => hold('<code>' + code.trim() + '</code>'));
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, url) => {
    const name = url.replace(/^\.\//, '');
    if (ctx.assets.has(name)) {
      return hold('<img src="/api/docs/assets/' + name + '" alt="' + alt + '" loading="lazy" decoding="async">');
    }
    return hold(alt);
  });
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
    const link = linkHref(url, ctx);
    const inner = emphasis(label);
    if (!link) return hold(inner);
    const attrs = link.external ? ' target="_blank" rel="noopener noreferrer"' : '';
    return hold('<a href="' + link.href + '"' + attrs + '>' + inner + '</a>');
  });
  s = emphasis(s);
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => held[Number(i)]);
}

// ── blocks ────────────────────────────────────────────────────────────────

const FENCE_OPEN = /^```\s*([A-Za-z0-9_+-]*)\s*$/;
const FENCE_CLOSE = /^```\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^(?:-{3,}|\*{3,}|_{3,})\s*$/;
const QUOTE = /^&gt;\s?(.*)$/;
const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

/** Cells of a table row, split on pipes outside code spans. */
function cells(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  const out = [];
  let cur = '', ticks = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '`') ticks ^= 1;
    if (c === '|' && !ticks && s[i - 1] !== '\\') { out.push(cur.trim()); cur = ''; continue; }
    cur += c;
  }
  out.push(cur.trim());
  return out;
}

function headingId(text, used) {
  const base = text.replace(/&[a-z0-9#]+;/g, '').replace(/[`*]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'section';
  let id = base, n = 1;
  while (used.has(id)) id = base + '-' + n++;
  used.add(id);
  return id;
}

/** Blocks over ESCAPED lines. A block quote's body is rendered by the same function. */
function blocks(lines, ctx) {
  const out = [];
  let para = [], list = null;
  const flushPara = () => {
    if (para.length) out.push('<p>' + inline(para.join(' '), ctx) + '</p>');
    para = [];
  };
  const itemHtml = (it) => {
    let t = it.text;
    const box = /^\[( |x|X)\]\s+(.*)$/.exec(t);
    let pre = '';
    if (box) {
      const done = box[1] !== ' ';
      pre = '<span class="md-task" role="img" aria-label="' + (done ? 'done' : 'not done') + '">'
        + (done ? '☑' : '☐') + '</span> ';
      t = box[2];
    }
    let html = pre + inline(t, ctx);
    if (it.sub.length) html += listHtml(it.subOrdered, it.subStart, it.sub);
    return '<li>' + html + '</li>';
  };
  const listHtml = (ordered, start, items) => {
    const tag = ordered ? 'ol' : 'ul';
    const at = ordered && start !== 1 ? ' start="' + start + '"' : '';
    return '<' + tag + at + '>' + items.map(itemHtml).join('') + '</' + tag + '>';
  };
  const flushList = () => {
    if (list) out.push(listHtml(list.ordered, list.start, list.items));
    list = null;
  };
  const flush = () => { flushPara(); flushList(); };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const fence = FENCE_OPEN.exec(raw);
    if (fence) {
      flush();
      const code = [];
      for (i++; i < lines.length && !FENCE_CLOSE.test(lines[i]); i++) code.push(lines[i]);
      const lang = fence[1] ? ' class="lang-' + fence[1].toLowerCase() + '"' : '';
      out.push('<pre><code' + lang + '>' + code.join('\n') + '</code></pre>');
      continue;
    }
    const line = raw.trim();
    if (!line) { flush(); continue; }

    const item = ITEM.exec(raw);
    if (item) {
      flushPara();
      const ordered = /\d/.test(item[2]);
      const indent = item[1].length;
      const text = item[3];
      if (list && indent > 0 && list.items.length) {
        const last = list.items[list.items.length - 1];
        if (!last.sub.length) { last.subOrdered = ordered; last.subStart = ordered ? parseInt(item[2], 10) : 1; }
        last.sub.push({ text, sub: [] });
        continue;
      }
      if (list && list.ordered !== ordered) flushList();
      if (!list) list = { ordered, start: ordered ? parseInt(item[2], 10) : 1, items: [] };
      list.items.push({ text, sub: [], subOrdered: false, subStart: 1 });
      continue;
    }
    const h = HEADING.exec(line);
    const rule = RULE.test(line);
    const quote = QUOTE.exec(line);
    const table = line.startsWith('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1].trim());
    // A line that starts no block of its own continues the list item above it.
    if (list && !h && !rule && !quote && !table && !FENCE_OPEN.test(raw)) {
      const last = list.items[list.items.length - 1];
      const target = last.sub.length ? last.sub[last.sub.length - 1] : last;
      target.text += ' ' + line;
      continue;
    }
    if (h) {
      flush();
      const lvl = Math.min(h[1].length + 1, 6); // # → h2: the page's h1 is its own
      out.push('<h' + lvl + ' id="' + headingId(h[2], ctx.ids) + '">' + inline(h[2], ctx) + '</h' + lvl + '>');
      continue;
    }
    if (rule) { flush(); out.push('<hr>'); continue; }
    if (quote) {
      flush();
      const body = [];
      for (; i < lines.length; i++) {
        const q = QUOTE.exec(lines[i].trim());
        if (!q) { i--; break; }
        body.push(q[1]);
      }
      out.push('<blockquote>' + blocks(body, ctx) + '</blockquote>');
      continue;
    }
    if (table) {
      flush();
      const head = cells(line);
      const align = cells(lines[i + 1].trim()).map((c) => (
        /^:-+:$/.test(c) ? ' class="al-c"' : /^-+:$/.test(c) ? ' class="al-r"' : ''));
      const rows = [];
      for (i += 2; i < lines.length && lines[i].trim().startsWith('|'); i++) rows.push(cells(lines[i]));
      i--;
      const td = (tag, c, k) => '<' + tag + (align[k] || '') + '>' + inline(c, ctx) + '</' + tag + '>';
      out.push('<div class="md-table"><table><thead><tr>' + head.map((c, k) => td('th', c, k)).join('')
        + '</tr></thead><tbody>'
        + rows.map((r) => '<tr>' + head.map((_, k) => td('td', r[k] == null ? '' : r[k], k)).join('') + '</tr>').join('')
        + '</tbody></table></div>');
      continue;
    }
    para.push(line);
  }
  flush();
  return out.join('\n');
}

/**
 * Markdown → HTML. `pages` maps a page file to its slug (for links between
 * pages); `assets` is the set of image files the folder carries.
 */
function renderMd(src, { pagesByFile = new Map(), assets = new Set() } = {}) {
  const ctx = { pagesByFile, assets, ids: new Set() };
  const lines = escapeHtml(String(src).replace(/\u0000/g, '').replace(/\r\n?/g, '\n')).split('\n');
  return blocks(lines, ctx);
}

// ── the folder ────────────────────────────────────────────────────────────

let cache = null;

/**
 * Every page the table names, rendered, in the table's order. Cached after
 * the first read: the files ship with the code, so a deploy is what changes
 * them. A folder, a table or a named page that cannot be read THROWS and is
 * not cached; the route answers 503, never an empty list.
 */
function listPages({ dir = DOCS_DIR } = {}) {
  if (cache && dir === DOCS_DIR) return cache;
  const files = fs.readdirSync(dir);
  const pages = parseSummary(fs.readFileSync(path.join(dir, SUMMARY), 'utf8'));
  const assets = new Set(files.filter((f) => ASSET_FILE.test(f)));
  const pagesByFile = new Map(pages.map((p) => [p.file, p.slug]));
  const out = pages.map((p) => {
    const src = fs.readFileSync(path.join(dir, p.file), 'utf8');
    return { ...p, html: renderMd(src, { pagesByFile, assets }), source: SOURCE + p.file };
  });
  if (dir === DOCS_DIR) cache = out;
  return out;
}

/** One page by slug, or null when the table names no such page. Throws when the folder is unreadable. */
function getPage(slug, opts) {
  if (typeof slug !== 'string' || !SLUG.test(slug)) return null;
  return listPages(opts).find((p) => p.slug === slug) || null;
}

/** The file behind an image name, or null: only a .png the folder carries is served. */
function assetPath(name, { dir = DOCS_DIR } = {}) {
  if (typeof name !== 'string' || !ASSET_FILE.test(name)) return null;
  const files = fs.readdirSync(dir);
  return files.includes(name) ? path.join(dir, name) : null;
}

const SHELL = path.join(__dirname, '..', 'public', 'docs.html');
let shell = null;

/**
 * The /docs shell for one address: { status, html }. The page script paints
 * the contents and the page; this sets what a crawler and a link preview read
 * before any script runs, so /docs/faq is titled and canonical as itself and
 * not as /docs. An address the table does not name is a 404. A folder that
 * cannot be read serves the plain shell, whose script then says so: an outage
 * is not a 404.
 */
function pageShell(slug, opts) {
  if (!shell) shell = fs.readFileSync(SHELL, 'utf8');
  if (slug == null || slug === '') return { status: 200, html: shell };
  let p = null;
  try {
    p = getPage(slug, opts);
  } catch (err) {
    return { status: 200, html: shell };
  }
  if (!p) return { status: 404, html: shell };
  const canon = /<link rel="canonical" href="(https:\/\/[^"]+)\/docs">/.exec(shell);
  if (!canon) throw new Error('docs.html has no canonical /docs link');
  const url = canon[1] + '/docs/' + p.slug;
  const title = escapeHtml(p.title) + ' — RUNECLAW docs';
  const swap = (html, re, to) => {
    if ((html.match(re) || []).length !== 1) throw new Error('docs.html: ' + re + ' does not match once');
    return html.replace(re, to);
  };
  let html = shell;
  html = swap(html, /<link rel="canonical" href="[^"]+">/g, '<link rel="canonical" href="' + url + '">');
  html = swap(html, /<meta property="og:url" content="[^"]+">/g, '<meta property="og:url" content="' + url + '">');
  html = swap(html, /<title>[^<]*<\/title>/g, '<title>' + title + '</title>');
  html = swap(html, /<meta property="og:title" content="[^"]+">/g, '<meta property="og:title" content="' + title + '">');
  html = swap(html, /<meta name="twitter:title" content="[^"]+">/g, '<meta name="twitter:title" content="' + title + '">');
  return { status: 200, html };
}

function _resetCache() { cache = null; shell = null; }

module.exports = {
  listPages, getPage, assetPath, pageShell, renderMd, parseSummary, slugOf, escapeHtml, linkHref, cells,
  DOCS_DIR, SOURCE, FOLDER, ASSET_FILE, _resetCache,
};
