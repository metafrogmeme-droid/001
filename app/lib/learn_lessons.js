'use strict';
/**
 * Lessons for the Study Room — served from docs/learn/*.md in this repo.
 *
 * The operator drops markdown files into docs/learn/ and they appear in the
 * room: filename (minus ordering prefix and .md) is the slug, the first
 * `# heading` is the title, file order is lesson order. Content is English
 * by design for now — the room's chrome speaks all 14 languages, the lessons
 * say so honestly rather than machine-translating a teaching text badly.
 *
 * Rendering is deliberately minimal and ESCAPE-FIRST: the entire source is
 * HTML-escaped before any markdown transform, so lesson files can never
 * inject markup beyond the small vocabulary here (headings, bold, italics,
 * lists, indented code blocks, paragraphs).
 */

const fs = require('fs');
const path = require('path');

const LESSONS_DIR = path.join(__dirname, '..', '..', 'docs', 'learn');

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

/** Escape-first markdown → HTML for the tiny vocabulary lessons use. */
function renderMd(src) {
  const lines = escapeHtml(String(src)).split('\n');
  const out = [];
  let para = [], list = null, code = null;
  const flushPara = () => {
    if (para.length) { out.push('<p>' + para.join(' ') + '</p>'); para = []; }
  };
  const flushList = () => {
    if (list) { out.push('<ul>' + list.map((li) => '<li>' + li + '</li>').join('') + '</ul>'); list = null; }
  };
  const flushCode = () => {
    if (code) { out.push('<pre><code>' + code.join('\n') + '</code></pre>'); code = null; }
  };
  const inline = (s) => s
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\*([^*]+)\*/g, '<i>$1</i>');
  for (const raw of lines) {
    if (/^    \S/.test(raw)) {           // 4-space indent = code
      flushPara(); flushList();
      (code = code || []).push(raw.slice(4));
      continue;
    }
    flushCode();
    const line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) {
      flushPara(); flushList();
      const lvl = h[1].length + 1;       // # → h2 on the page (h1 is the room's)
      out.push(`<h${lvl}>` + inline(h[2]) + `</h${lvl}>`);
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      flushPara();
      (list = list || []).push(inline(line.replace(/^[-*]\s+/, '')));
      continue;
    }
    flushList();
    para.push(inline(line));
  }
  flushPara(); flushList(); flushCode();
  return out.join('\n');
}

let cache = null;
/** All lessons, ordered by filename. Cached after the first read — lesson
 *  files ship with the code, so a deploy is what changes them. */
function listLessons() {
  if (cache) return cache;
  let files = [];
  try {
    files = fs.readdirSync(LESSONS_DIR).filter((f) => f.endsWith('.md')).sort();
  } catch (e) { files = []; }
  cache = files.map((f) => {
    const src = fs.readFileSync(path.join(LESSONS_DIR, f), 'utf8');
    const slug = f.replace(/^\d+-/, '').replace(/\.md$/, '');
    const title = (src.match(/^#\s+(.*)$/m) || [null, slug])[1];
    const { body, quiz } = splitQuiz(src);
    return { slug, file: f, title, html: renderMd(body), text: src, quiz };
  });
  return cache;
}

/**
 * Self-check quizzes ride INSIDE the lesson markdown, in an authoring
 * convention the operator can follow when supplying docs:
 *
 *   ## Self-check
 *   1. The question?
 *   - [ ] a wrong option
 *   - [x] the right option
 *
 * The section is stripped from the rendered lesson body and returned as
 * structured data — the page grades it client-side by arithmetic, never by
 * mercy. Self-check only: answers ship with the quiz (this is a mirror for
 * the reader, not a certification), and a lesson without the section simply
 * has quiz: [] — nothing is invented. A malformed question (no [x], or
 * several) is DROPPED rather than guessed.
 */
function splitQuiz(src) {
  const m = /^##\s+Self-check\s*$/m.exec(src);
  if (!m) return { body: src, quiz: [] };
  const body = src.slice(0, m.index).trimEnd() + '\n';
  const section = src.slice(m.index + m[0].length);
  const quiz = [];
  let current = null;
  for (const raw of section.split('\n')) {
    const line = raw.trim();
    const q = /^\d+\.\s+(.*)$/.exec(line);
    const o = /^-\s+\[( |x)\]\s+(.*)$/.exec(line);
    if (q) {
      if (current) quiz.push(current);
      current = { q: q[1], options: [], answer: -1, checks: 0 };
    } else if (o && current) {
      if (o[1] === 'x') { current.answer = current.options.length; current.checks++; }
      current.options.push(o[2]);
    }
  }
  if (current) quiz.push(current);
  return {
    body,
    quiz: quiz
      .filter((x) => x.options.length >= 2 && x.checks === 1)
      .map(({ q, options, answer }) => ({ q, options, answer })),
  };
}

function getLesson(slug) {
  return listLessons().find((l) => l.slug === String(slug || '')) || null;
}

// Test seam: drop the cache so a test can point at fresh fixtures.
function resetCache() { cache = null; }

module.exports = { listLessons, getLesson, renderMd, resetCache, LESSONS_DIR };
