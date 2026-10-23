'use strict';
/**
 * The sentences the pre-check is willing to say.
 *
 * Tone is the colour, and colour is a claim. There is no "good" tone.
 * `caution` names a power or a state an investor can hit. `neutral` names
 * something that was read. `muted` names a negative that is not a proof
 * ("not observed" is not "absent") or a reading that could not be finished.
 *
 * The disclaimer is the same string the page prints. A second wording would
 * let the API say one thing and the page another.
 */

const DISCLAIMER = 'This is an automated pre-check of a tokenized-asset contract. '
  + 'It is not an audit and it is not investment advice. '
  + 'It does not issue, rate, recommend or promote any asset.';

const TONE = {
  'contract:empty': 'caution',
  'selectors:not_evm': 'muted',
  'erc3643:detected': 'neutral',
  'erc3643:not_detected': 'neutral',
  'erc3643:not_observed': 'muted',
  'identity_registry:set': 'neutral',
  'identity_registry:unset': 'neutral',
  'identity_registry:not_observed': 'muted',
  'compliance:set': 'neutral',
  'compliance:unset': 'neutral',
  'compliance:not_observed': 'muted',
  'compliance_modules:set': 'neutral',
  'compliance_modules:empty': 'neutral',
  'identity_topics:set': 'neutral',
  'identity_topics:unset': 'neutral',
  'identity_issuers:set': 'neutral',
  'identity_issuers:unset': 'neutral',
  'identity_storage:set': 'neutral',
  'identity_storage:unset': 'neutral',
  'paused:yes': 'caution',
  'paused:no': 'neutral',
  'owner:set': 'neutral',
  'owner:unset': 'neutral',
  'owner:not_observed': 'muted',
  'upgradeable:yes': 'caution',
  'upgradeable:not_observed': 'muted',
  'proxy_admin:set': 'caution',
  'forced_transfer:observed': 'caution',
  'forced_transfer:not_observed': 'muted',
  'freeze:observed': 'caution',
  'freeze:not_observed': 'muted',
  'pause_control:observed': 'caution',
  'pause_control:not_observed': 'muted',
  'mint_burn:observed': 'caution',
  'mint_burn:not_observed': 'muted',
  'agent_admin:observed': 'caution',
  'agent_admin:not_observed': 'muted',
  'can_transfer:observed': 'muted',
  'can_transfer:not_observed': 'muted',
  'oracle:stale': 'caution',
  'oracle:within_heartbeat': 'neutral',
  'oracle:unknown': 'muted',
  'oracle:none': 'neutral',
  'investor:not_checked': 'neutral',
  'investor_verified:yes': 'neutral',
  'investor_verified:no': 'caution',
  'investor_frozen:yes': 'caution',
  'investor_frozen:no': 'neutral',
  'investor_agent:yes': 'caution',
  'investor_agent:no': 'neutral',
};

function toneOf(id, state) {
  const tone = TONE[id + ':' + state];
  return tone || null;
}

module.exports = { DISCLAIMER, TONE, toneOf };
