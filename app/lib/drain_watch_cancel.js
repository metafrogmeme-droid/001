'use strict';
/**
 * Cancel links belong to an on-chain module. That module is not installed.
 *
 * The answer is that there is no transaction to sign. A later module can
 * replace this function. It must not grow a relay or a stored signature.
 */

function cancelLink() {
  return {
    available: false,
    reason: 'module_not_installed',
    tx: null,
    url: null,
  };
}

module.exports = { cancelLink: cancelLink };
