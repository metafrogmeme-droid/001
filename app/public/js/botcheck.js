'use strict';

/**
 * Paints /botcheck from /api/botcheck. Text is assigned with textContent.
 * A link is set only when the source is https. A summary without the
 * AI-written notice is left out.
 */

var ALLOWED_ERRORS = [
  'That name cannot be checked.',
  'The check could not be read.',
  'Send a bot name.',
  'That note cannot be queued.',
  'Send a bot name, not a numeric id. Nothing was stored.',
  'That name is already in the curated list. Nothing was added.',
];

function answerClass(answer) {
  return answer === 'unknown' ? 'ans muted' : 'ans';
}

function httpsHref(url) {
  if (typeof url !== 'string') return '';
  if (url.indexOf('https://') !== 0 || url.indexOf(' ') !== -1) return '';
  return url;
}

function shownError(status, body) {
  if (status === 404) return 'This check is not available.';
  if (status === 429) return 'Slow down a little and try again.';
  var err = body && typeof body.error === 'string' ? body.error : '';
  return ALLOWED_ERRORS.indexOf(err) !== -1 ? err : 'The check could not be read.';
}

function el(tag, className, text) {
  var node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function sourceLink(url, label) {
  var href = httpsHref(url);
  if (!href) return el('p', 'detail', label || '');
  var p = el('p', 'detail');
  var a = document.createElement('a');
  a.className = 'src';
  a.href = href;
  a.rel = 'noopener noreferrer';
  a.textContent = label || href;
  p.appendChild(a);
  return p;
}

function paintSummary(box, summary) {
  if (!summary || typeof summary.notice !== 'string' || summary.notice.indexOf('AI-written') !== 0) return;
  box.appendChild(el('p', 'ai', summary.notice));
  if (typeof summary.text === 'string' && summary.text) box.appendChild(el('p', 'detail', summary.text));
}

function paintBot(box, bot, disclaimer) {
  box.replaceChildren();
  if (disclaimer) box.appendChild(el('p', 'lede', disclaimer));
  box.appendChild(el('h2', null, bot.name));
  var handles = Array.isArray(bot.usernames) ? bot.usernames : [];
  if (handles.length) {
    box.appendChild(el('p', 'note', handles.map(function (h) { return '@' + String(h).replace(/^@/, ''); }).join(' ')));
  }
  (bot.signals || []).forEach(function (row) {
    var block = el('section', 'sig');
    block.appendChild(el('h3', null, row.question || ''));
    block.appendChild(el('p', answerClass(row.answer), row.answer_text || ''));
    if (row.id === 'code_public' && row.maintained) {
      block.appendChild(el('p', answerClass(row.maintained), 'Maintained: ' + row.maintained));
    }
    block.appendChild(el('p', 'detail', row.detail || ''));
    block.appendChild(sourceLink(row.source_url, row.source_label));
    if (row.maintained_source_url) {
      block.appendChild(sourceLink(row.maintained_source_url, row.maintained_source_label || 'Maintained source'));
    }
    if (row.as_of) block.appendChild(el('p', 'note', 'as of ' + row.as_of));
    box.appendChild(block);
  });
  paintSummary(box, bot.summary);
}

function paintMiss(box, result) {
  box.replaceChildren();
  if (result && result.disclaimer) box.appendChild(el('p', 'lede', result.disclaimer));
  box.appendChild(el('p', null, 'No curated entry for that name.'));
  box.appendChild(el('p', null, 'It is unknown. Nothing here was filled in.'));
  box.appendChild(el('p', 'note', 'Use the queue below. A queued name is not published.'));
}

function paintList(box, doc) {
  box.replaceChildren();
  if (doc && doc.disclaimer) box.appendChild(el('p', 'lede', doc.disclaimer));
  box.appendChild(el('p', null, 'Send a name, or pick one from the list.'));
  var ul = el('ul', 'names');
  (doc && doc.bots ? doc.bots : []).forEach(function (bot) {
    var li = el('li');
    var button = el('button', 'name', bot.name || '');
    button.type = 'button';
    button.setAttribute('data-name', bot.name || '');
    li.appendChild(button);
    ul.appendChild(li);
  });
  box.appendChild(ul);
}

function paintError(box, text) {
  box.replaceChildren();
  box.appendChild(el('p', null, text));
}

function readJson(url, options) {
  return fetch(url, options).then(function (res) {
    return res.json().then(function (body) {
      return { status: res.status, body: body };
    }, function () {
      return { status: res.status, body: null };
    });
  }, function () {
    return { status: 0, body: null };
  });
}

function runLookup(name, box) {
  var url = '/api/botcheck?q=' + encodeURIComponent(name);
  return readJson(url).then(function (got) {
    if (got.status !== 200 || !got.body || got.body.ok !== true) {
      paintError(box, shownError(got.status, got.body));
      return;
    }
    if (!got.body.found || !got.body.bot) paintMiss(box, got.body);
    else paintBot(box, got.body.bot, got.body.disclaimer);
  });
}

function runList(box) {
  return readJson('/api/botcheck').then(function (got) {
    if (got.status !== 200 || !got.body || got.body.ok !== true) {
      paintError(box, shownError(got.status, got.body));
      return;
    }
    paintList(box, got.body);
  });
}

function boot() {
  var out = document.getElementById('out');
  var input = document.getElementById('q');
  var params = new URLSearchParams(window.location.search);
  var initial = params.get('q') || '';
  if (initial && input) input.value = initial;
  if (initial) runLookup(initial, out);
  else runList(out);

  var lookupForm = document.getElementById('lookup-form');
  if (lookupForm) {
    lookupForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var name = input ? input.value : '';
      if (String(name).trim()) runLookup(name, out);
      else runList(out);
    });
  }

  if (out) {
    out.addEventListener('click', function (ev) {
      var button = ev.target && ev.target.closest ? ev.target.closest('button[data-name]') : null;
      if (!button || !input) return;
      input.value = button.getAttribute('data-name') || '';
      if (input.value) runLookup(input.value, out);
    });
  }

  var submitForm = document.getElementById('submit-form');
  var status = document.getElementById('submit-status');
  if (submitForm) {
    submitForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var name = document.getElementById('submit-name');
      var note = document.getElementById('submit-note');
      var payload = {
        query: name ? name.value : '',
        note: note ? note.value : '',
      };
      readJson('/api/botcheck/submit', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(function (got) {
        if (!status) return;
        if (got.status === 200 && got.body && got.body.queued === true) {
          status.textContent = 'Queued for review. It is not published.';
          return;
        }
        status.textContent = shownError(got.status, got.body);
      });
    });
  }
}

if (typeof document !== 'undefined') boot();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    answerClass: answerClass,
    httpsHref: httpsHref,
    shownError: shownError,
    ALLOWED_ERRORS: ALLOWED_ERRORS,
  };
}
