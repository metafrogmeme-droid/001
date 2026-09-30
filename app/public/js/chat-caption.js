/**
 * The "read: …" caption under a chat answer.
 *
 * The server's `read_from` is the sentence both surfaces share. When an older
 * gateway omits it, the same sentence is built from the tool list: a tool
 * that did not succeed is marked, not hidden.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCChatCaption = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  function readFromCaption(data) {
    const fromServer = data && typeof data.read_from === 'string'
      ? data.read_from.trim() : '';
    if (fromServer) return fromServer;
    const tools = data && Array.isArray(data.tools) ? data.tools : [];
    const names = tools.map(function (t) {
      if (!t || !t.name) return '';
      return String(t.name) + (t.ok === false ? '\u2717' : '');
    }).filter(Boolean);
    return names.length ? ('read: ' + names.join(', ')) : '';
  }
  return { readFromCaption: readFromCaption };
});
