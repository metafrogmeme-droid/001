/**
 * Pi Sign-In's redirect URI page (/auth/pi/callback, lib/pi_signin.js).
 *
 * Pi returns the access token in the URL fragment, which never reaches a
 * server. This script reads it once, removes it from the address bar and the
 * history entry before anything else, and hands the token and Pi's echo of
 * our state to the server, which checks both and answers the same session
 * payload the other sign-ins land on the home page with (`/#oauth=`).
 *
 * Every failure goes to the home page's own error line (`/#oauth_error=`),
 * with a sentence chosen here, never text copied from the URL.
 */
(function () {
  'use strict';
  var params = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, '', location.pathname);

  function fail(msg) {
    location.replace('/#oauth_error=' + encodeURIComponent(msg));
  }

  var error = params.get('error');
  if (error) {
    fail(error === 'access_denied' || error === 'cancelled'
      ? 'Pi sign-in was cancelled.'
      : error === 'expired'
        ? 'The Pi sign-in expired. Please try again.'
        : 'Pi could not complete the sign-in. Please try again.');
    return;
  }
  var token = params.get('access_token');
  var state = params.get('state');
  if (!token || !state) {
    fail('Pi did not return a sign-in. Please try again.');
    return;
  }

  fetch('/api/auth/oauth/pi/token', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ access_token: token, state: state }),
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (body) {
      if (r.ok && body && body.oauth) {
        location.replace('/#oauth=' + body.oauth);
      } else if (r.status === 503) {
        fail('Pi could not be reached to confirm the sign-in. This is on our side; please try again.');
      } else if (r.status === 401) {
        fail('Pi did not confirm the sign-in.');
      } else if (r.status === 400) {
        fail('This sign-in link expired or was already used. Please try again.');
      } else {
        fail('The sign-in could not be completed. Please try again.');
      }
    });
  }, function () {
    fail('RUNECLAW could not be reached to finish the sign-in. Please try again.');
  });
})();
