'use strict';
/**
 * Which Pi Developer Portal app this request is for.
 *
 * The Mainnet app and a Testnet app are two apps on one server. The Testnet
 * app's hostname is PI_TESTNET_HOST (for example pi.humanoid-traders.com).
 * Every other host, including www, is the Mainnet app.
 *
 * The hostname is Express's req.hostname. That is the Host header with the
 * port removed, and X-Forwarded-Host only when this process's trust proxy
 * already trusts the connecting hop (server.js, TRUSTED_PROXY). A second
 * reading of that header here would trust a caller the rest of the server
 * does not. Hostnames are case-insensitive; the setting may name a port.
 */

function requestHost(req) {
  return String((req && req.hostname) || '').trim().toLowerCase().replace(/:\d+$/, '');
}

function testnetHost(env = process.env) {
  return String((env && env.PI_TESTNET_HOST) || '').trim().toLowerCase().replace(/:\d+$/, '');
}

function isTestnetHost(req, env = process.env) {
  const want = testnetHost(env);
  return want.length > 0 && requestHost(req) === want;
}

module.exports = { requestHost, testnetHost, isTestnetHost };
