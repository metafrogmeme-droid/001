'use strict';
/**
 * Evidence tab for Agent Check to mount.
 *
 * Counts stay split. A reading that was not indexed does not render as zero.
 * A summary, when one is present, is labelled AI-written. This file is not
 * served on its own.
 */

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function countText(counts, key) {
  if (!counts || typeof counts !== 'object') return 'unreadable';
  const n = counts[key];
  if (typeof n !== 'number' || !Number.isFinite(n)) return 'unreadable';
  return String(n);
}

function renderEvidenceTab(reading) {
  const row = reading && typeof reading === 'object' ? reading : null;
  if (!row || row.indexed !== true || !row.counts || typeof row.counts !== 'object') {
    const note = row && typeof row.note === 'string' && row.note
      ? row.note
      : 'This agent is not in the local index.';
    return '<section id="grounded-reputation-evidence"><p>' + esc(note) + '</p></section>';
  }
  const c = row.counts;
  const lines = [
    '<section id="grounded-reputation-evidence">',
    '<h2>Evidence</h2>',
    '<dl>',
    '<dt>Backed</dt><dd>' + countText(c, 'backed_direct') + '</dd>',
    '<dt>' + esc(row.escrow_label || 'escrow, can\'t attribute') + '</dt><dd>' + countText(c, 'escrow') + '</dd>',
    '<dt>Proof failed</dt><dd>' + countText(c, 'failed') + '</dd>',
    '<dt>Tampered</dt><dd>' + countText(c, 'tampered') + '</dd>',
    '<dt>No proof</dt><dd>' + countText(c, 'none') + '</dd>',
    '<dt>File unreadable</dt><dd>' + countText(c, 'uri_unresolvable') + '</dd>',
    '<dt>Revoked</dt><dd>' + countText(c, 'revoked') + '</dd>',
    '</dl>',
  ];
  lines.push(bucketBlock('Value moved, direct', row.value_buckets, row.buckets_unreadable));
  lines.push(bucketBlock('Value moved into escrow', row.escrow_buckets, row.buckets_unreadable));
  if (typeof row.bucket_basis === 'string') {
    lines.push('<p>' + esc(row.bucket_basis) + '</p>');
  }
  lines.push(signalBlock(row.signals));
  if (typeof row.summary === 'string' && row.summary.trim() !== '') {
    lines.push('<p>AI-written summary. ' + esc(row.summary) + '</p>');
  }
  if (typeof row.limits === 'string') lines.push('<p>' + esc(row.limits) + '</p>');
  if (typeof row.footer === 'string') lines.push('<p>' + esc(row.footer) + '</p>');
  lines.push('</section>');
  return lines.join('');
}

function bucketBlock(title, buckets, unreadable) {
  if (unreadable === true || buckets == null) {
    return '<h3>' + esc(title) + '</h3><p>Value buckets could not be read.</p>';
  }
  if (!Array.isArray(buckets)) {
    return '<h3>' + esc(title) + '</h3><p>Value buckets could not be read.</p>';
  }
  const items = buckets.map((bucket) => {
    const label = bucket && bucket.label != null ? bucket.label : 'unreadable';
    const count = bucket && typeof bucket.count === 'number' && Number.isFinite(bucket.count)
      ? String(bucket.count)
      : 'unreadable';
    return '<li>' + esc(label) + ': ' + esc(count) + '</li>';
  });
  return '<h3>' + esc(title) + '</h3><ul>' + items.join('') + '</ul>';
}

function signalBlock(signals) {
  if (!signals || typeof signals !== 'object') {
    return '<h3>Graph signals</h3><p>Graph signals could not be read.</p>';
  }
  const keys = ['self_loop_suspect', 'common_first_funder', 'owner_adjacent', 'burst', 'wash'];
  const parts = ['<h3>Graph signals</h3>'];
  for (const key of keys) {
    const rows = signals[key];
    parts.push('<h4>' + esc(key) + '</h4>');
    if (rows == null) {
      parts.push('<p>Could not be read.</p>');
      continue;
    }
    if (!Array.isArray(rows) || rows.length === 0) {
      parts.push('<p>None found.</p>');
      continue;
    }
    parts.push('<ul>' + rows.map((item) => {
      const text = item && typeof item.text === 'string' ? item.text : 'unreadable';
      return '<li>' + esc(text) + '</li>';
    }).join('') + '</ul>');
  }
  return parts.join('');
}

module.exports = { renderEvidenceTab };
