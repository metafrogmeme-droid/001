/**
 * /docs: the table of contents and one page of the documentation.
 *
 * The page HTML arrives rendered from the server (app/lib/docs_pages.js,
 * escape-first), so this file only places it. The two halves fail apart: a
 * table of contents that could not be read does not blank the page beside it,
 * and a page that could not be read still leaves the table to choose from.
 * Neither failure is painted as "no docs": each says what could not be read
 * and links the same folder on GitHub.
 *
 * Dual export: browser (window.DocsPage) + Node (require), so the test drives
 * the same render the visitor sees.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DocsPage = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const FOLDER = 'https://github.com/metafrogmeme-droid/001/tree/main/docs/gitbook';
  const SLUG = /^[a-z0-9][a-z0-9-]*$/;
  const TOC_UNREAD = 'The table of contents could not be read right now.';
  const PAGE_UNREAD = 'This page could not be read right now. Try again in a minute.';
  const NO_PAGE = 'There is no documentation page at this address. The contents list every page.';
  const ON_GITHUB = 'Read the documentation on GitHub';
  const SOURCE = 'This page on GitHub';
  const ENGLISH = 'The documentation is written in English.';
  const PREV = '← Previous';
  const NEXT = 'Next →';

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function say(key, en) {
    try {
      const i18n = (typeof window !== 'undefined') ? window.RCI18N : null;
      const v = i18n ? i18n.translate(key, i18n.getLang()) : '';
      return v || en;
    } catch (e) {
      return en;
    }
  }

  function text(key, en) {
    return '<span data-i18n="' + key + '">' + esc(say(key, en)) + '</span>';
  }

  /** The slug in /docs/<slug>, '' for /docs itself, null for a path that names no page. */
  function slugFromPath(pathname) {
    const m = /^\/docs(?:\/([^/]*))?\/?$/.exec(String(pathname || ''));
    if (!m) return null;
    const s = m[1] ? decodeURIComponent(m[1]) : '';
    return s === '' || SLUG.test(s) ? s : null;
  }

  /** A pages list is readable only when every entry has a slug and a title. */
  function readablePages(body) {
    if (!body || !Array.isArray(body.pages) || !body.pages.length) return null;
    for (const p of body.pages) {
      if (!p || typeof p.slug !== 'string' || !SLUG.test(p.slug) || typeof p.title !== 'string' || !p.title) return null;
    }
    return body.pages;
  }

  function githubLink() {
    return '<a href="' + FOLDER + '" target="_blank" rel="noopener noreferrer">' + text('docs.on_github', ON_GITHUB) + '</a>';
  }

  function retryControl() {
    return ' <button type="button" class="btn btn--sm" data-docs-retry>Retry</button>';
  }

  function tocHtml(pages, current) {
    if (!pages) return '<p class="doc-absent">' + text('docs.toc_unread', TOC_UNREAD) + ' ' + githubLink() + retryControl() + '</p>';
    return '<ol dir="ltr" lang="en">' + pages.map(function (p) {
      const here = p.slug === current ? ' aria-current="page"' : '';
      return '<li><a href="/docs/' + p.slug + '"' + here + '>' + esc(p.title) + '</a></li>';
    }).join('') + '</ol>';
  }

  function pagerHtml(pages, current) {
    if (!pages) return '';
    const i = pages.findIndex(function (p) { return p.slug === current; });
    if (i < 0) return '';
    const prev = pages[i - 1], next = pages[i + 1];
    return '<nav class="doc-pager">'
      + (prev ? '<a href="/docs/' + prev.slug + '" rel="prev">' + text('docs.prev', PREV) + ' ' + esc(prev.title) + '</a>' : '<span></span>')
      + (next ? '<a href="/docs/' + next.slug + '" rel="next">' + esc(next.title) + ' ' + text('docs.next', NEXT) + '</a>' : '<span></span>')
      + '</nav>';
  }

  /**
   * The article for one answer: 200 with a page, 404, or anything else.
   * `res` is { status, body }.
   */
  function pageHtml(res, pages) {
    const b = res && res.body;
    if (res && res.status === 200 && b && typeof b.html === 'string' && typeof b.slug === 'string'
        && typeof b.source === 'string' && /^https:\/\/github\.com\/metafrogmeme-droid\/001\/blob\/main\/docs\/gitbook\/[A-Za-z0-9-]+\.md$/.test(b.source)) {
      // The docs are English: they keep their own direction inside a right-to-left page.
      return '<div class="doc-body" dir="ltr" lang="en">' + b.html + '</div>'
        + '<div class="doc-meta"><span>' + text('docs.english', ENGLISH) + '</span>'
        + '<a href="' + esc(b.source) + '" target="_blank" rel="noopener noreferrer">' + text('docs.source', SOURCE) + '</a></div>'
        + pagerHtml(pages, b.slug);
    }
    if (res && res.status === 404) {
      return '<p class="doc-absent">' + text('docs.no_page', NO_PAGE) + '</p>';
    }
    return '<p class="doc-absent">' + text('docs.page_unread', PAGE_UNREAD) + ' ' + githubLink() + retryControl() + '</p>';
  }

  function getJson(url) {
    return fetch(url, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(10000) })
      .then(function (r) {
        return r.json().then(function (body) {
          return { status: r.status, body: body };
        }, function () {
          return { status: r.status, body: null };
        });
      })
      .catch(function () { return { status: 0, body: null }; });
  }

  function boot(doc, loc) {
    if (!doc.__docsRetry) {
      doc.__docsRetry = true;
      doc.addEventListener('click', function (e) {
        const b = e.target && e.target.closest && e.target.closest('[data-docs-retry]');
        if (b) boot(doc, loc);
      });
    }
    const tocEl = doc.getElementById('doc-toc');
    const pageEl = doc.getElementById('doc-page');
    const asked = slugFromPath(loc.pathname);
    // On a phone the contents start folded, so the page is the first thing on screen.
    const fold = doc.getElementById('doc-toc-d');
    if (fold && typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(max-width: 820px)').matches) fold.open = false;
    getJson('/api/docs/pages').then(function (listRes) {
      const pages = listRes.status === 200 ? readablePages(listRes.body) : null;
      // /docs is the first page in the table; with no table, the front page by name.
      const slug = asked === '' ? (pages ? pages[0].slug : 'introduction') : asked;
      tocEl.innerHTML = tocHtml(pages, slug);
      if (slug === null) { pageEl.innerHTML = pageHtml({ status: 404, body: null }, pages); return; }
      return getJson('/api/docs/pages/' + slug).then(function (res) {
        pageEl.innerHTML = pageHtml(res, pages);
        if (res.status === 200 && res.body && typeof res.body.title === 'string') {
          doc.title = res.body.title + ' — RUNECLAW docs';
          // The page arrived after the browser looked for #part; look again.
          const id = /^#([a-z0-9][a-z0-9-]*)$/.exec(loc.hash || '');
          const target = id && doc.getElementById(id[1]);
          if (target && target.scrollIntoView) target.scrollIntoView();
        }
      });
    });
  }

  if (typeof document !== 'undefined' && document.getElementById && document.getElementById('doc-page')) {
    boot(document, window.location);
  }

  return {
    slugFromPath, readablePages, tocHtml, pagerHtml, pageHtml, boot,
    FOLDER, TOC_UNREAD, PAGE_UNREAD, NO_PAGE, ON_GITHUB, SOURCE, ENGLISH, PREV, NEXT,
  };
}));
