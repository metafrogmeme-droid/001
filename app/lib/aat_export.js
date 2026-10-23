'use strict';
/**
 * Export one existing proof bundle as an IETF Agent Audit Trail record.
 *
 * Draft: draft-sharif-agent-audit-trail-08 (13 October 2026)
 * https://datatracker.ietf.org/doc/html/draft-sharif-agent-audit-trail-08
 *
 * The bundle is the rcproof/1 document tools/rcverify.js already checks.
 * This adds a record beside it. It does not rewrite seal, seal_payload or
 * the Merkle path, and it does not sign: on mainnet this server holds no
 * key. The record is hashes and categories taken from that bundle. A
 * Telegram id, a stake and a dollar amount are not fields of it.
 *
 * TWO PLACES THE DRAFT ASKS FOR SOMETHING THIS EXPORT DOES NOT DO
 *
 * record_id and session_id are UUIDv4-shaped values derived from the seal
 * (version and variant bits set as RFC 9562 describes). The draft says a
 * fresh random UUIDv4. A random id would be new data, different on every
 * download, and we do not store one. The same receipt therefore exports
 * the same record.
 *
 * A session's first record, in section 8.1, is a lifecycle session_start.
 * This export is one decision record for one receipt that was already
 * sealed. parent_record_id and prev_hash are null because there is no
 * previous audit record. A synthetic session_start would be a record the
 * receipt does not contain.
 *
 * record_phase is post_execution. The action in the record is the
 * classification the seal already holds, and that classification has
 * finished. pre_execution with outcome success would read as "the send was
 * authorised", which a warning is not. outcome is success because the
 * classification was produced, not because a send was allowed. trust_level
 * is L0: this server does not hold a key for the agent and does not sign
 * the record.
 */

const crypto = require('node:crypto');

const AAT_DRAFT = 'draft-sharif-agent-audit-trail-08';
const AAT_DRAFT_DATE = '2026-10-13';
const AAT_DRAFT_URL = 'https://datatracker.ietf.org/doc/html/draft-sharif-agent-audit-trail-08';

const ACTION_TYPES = ['tool_call', 'tool_response', 'decision', 'delegation', 'escalation', 'error', 'lifecycle'];
const OUTCOMES = ['success', 'failure', 'timeout', 'denied', 'escalated'];
const PHASES = ['pre_execution', 'post_execution', 'concurrent'];
const TRUST = ['L0', 'L1', 'L2', 'L3', 'L4'];
const LEVELS = ['stop', 'check', 'clear', 'unknown', 'invalid'];
const TOOLS = ['claw_guard', 'xray_transaction', 'scan_transaction'];
const HEX64 = /^[0-9a-f]{64}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const RFC3339_Z = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;

function uuidV4From(material) {
  const buf = crypto.createHash('sha256').update(String(material), 'utf8').digest().subarray(0, 16);
  buf[6] = (buf[6] & 0x0f) | 0x40;
  buf[8] = (buf[8] & 0x3f) | 0x80;
  const h = Buffer.from(buf).toString('hex');
  return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
}

function iso(t) {
  if (t == null || t === '') return null;
  const d = new Date(t);
  if (!Number.isFinite(d.getTime())) return null;
  return d.toISOString();
}

function parsePayload(bundle) {
  if (!bundle || typeof bundle.seal_payload !== 'string') return null;
  try {
    const p = JSON.parse(bundle.seal_payload);
    return p && typeof p === 'object' ? p : null;
  } catch (e) {
    return null;
  }
}

function agentId(payload) {
  const tool = payload && typeof payload.tool === 'string' ? payload.tool : '';
  if (TOOLS.includes(tool)) return 'urn:agent:runeclaw:' + tool;
  const kind = payload && typeof payload.kind === 'string' ? payload.kind : '';
  if (kind === 'presign_verdict' || kind === 'presign_scan') return 'urn:agent:runeclaw:claw-guard';
  return 'urn:agent:runeclaw:receipt';
}

/**
 * The audit record for one bundle, or { ok:false, error } when the bundle
 * does not carry a readable payload and a readable time. Missing is not
 * filled in.
 */
function aatRecord(bundle) {
  const b = bundle || {};
  const seal = typeof b.seal === 'string' ? b.seal.toLowerCase() : '';
  if (!HEX64.test(seal)) return { ok: false, error: 'seal' };
  const payload = parsePayload(b);
  if (!payload) return { ok: false, error: 'payload' };
  const version = payload.v;
  if (typeof version !== 'number' || !Number.isInteger(version) || version < 0 || version > 999) {
    return { ok: false, error: 'version' };
  }
  const timestamp = iso(b.sealed_at) || iso(payload.scanned_at);
  if (!timestamp) return { ok: false, error: 'time' };

  const detail = { decision_type: 'classify' };
  if (typeof payload.kind === 'string' && /^[a-z0-9_]{1,40}$/.test(payload.kind)) {
    detail.receipt_kind = payload.kind;
  }
  if (typeof payload.level === 'string' && LEVELS.includes(payload.level)) {
    detail.verdict_level = payload.level;
  }

  const record = {
    record_id: uuidV4From(seal + ':aat-record'),
    timestamp: timestamp,
    agent_id: agentId(payload),
    principal: {
      principal_type: 'human',
      principal_id: 'urn:user:role:signed-in-caller',
      authority_ref: null,
    },
    agent_version: version + '.0.0',
    session_id: uuidV4From(seal + ':aat-session'),
    action_type: 'decision',
    action_detail: detail,
    outcome: 'success',
    trust_level: 'L0',
    parent_record_id: null,
    prev_hash: null,
    record_phase: 'post_execution',
    output_hash: seal,
    recording_component: 'urn:component:runeclaw:aat-export',
  };
  if (typeof payload.input_sha256 === 'string' && HEX64.test(payload.input_sha256)) {
    record.input_hash = payload.input_sha256;
  }
  const errors = validateAatRecord(record);
  if (errors.length) return { ok: false, error: 'record' };
  return { ok: true, record: record };
}

/**
 * The bundle plus the record. seal, seal_payload and merkle are the same
 * objects and strings the bundle already had.
 */
function exportAat(bundle) {
  const made = aatRecord(bundle);
  if (!made.ok) return made;
  return {
    ok: true,
    body: Object.assign({}, bundle, {
      aat_draft: AAT_DRAFT,
      aat_draft_date: AAT_DRAFT_DATE,
      aat_draft_url: AAT_DRAFT_URL,
      aat: made.record,
    }),
  };
}

function validateAatRecord(record) {
  const errors = [];
  const r = record && typeof record === 'object' ? record : null;
  if (!r) return ['record'];
  const need = ['record_id', 'timestamp', 'agent_id', 'principal', 'agent_version', 'session_id',
    'action_type', 'action_detail', 'outcome', 'trust_level', 'parent_record_id', 'prev_hash', 'record_phase'];
  for (const k of need) {
    if (!(k in r)) errors.push('missing ' + k);
  }
  if (typeof r.record_id !== 'string' || !UUID_V4.test(r.record_id)) errors.push('record_id');
  if (typeof r.session_id !== 'string' || !UUID_V4.test(r.session_id)) errors.push('session_id');
  if (r.record_id && r.record_id === r.session_id) errors.push('session_id distinct');
  if (typeof r.timestamp !== 'string' || !RFC3339_Z.test(r.timestamp)) errors.push('timestamp');
  if (typeof r.agent_id !== 'string' || !r.agent_id.startsWith('urn:')) errors.push('agent_id');
  if (typeof r.agent_version !== 'string' || !/^\d+\.\d+\.\d+$/.test(r.agent_version)) errors.push('agent_version');
  const p = r.principal;
  if (!p || typeof p !== 'object'
    || !['human', 'organization', 'service', 'agent'].includes(p.principal_type)
    || typeof p.principal_id !== 'string' || !p.principal_id.startsWith('urn:')
    || !('authority_ref' in p) || (p.authority_ref !== null && typeof p.authority_ref !== 'string')) {
    errors.push('principal');
  }
  if (!ACTION_TYPES.includes(r.action_type)) errors.push('action_type');
  if (!OUTCOMES.includes(r.outcome)) errors.push('outcome');
  if (!TRUST.includes(r.trust_level)) errors.push('trust_level');
  if (!PHASES.includes(r.record_phase)) errors.push('record_phase');
  if (r.parent_record_id !== null && (typeof r.parent_record_id !== 'string' || !UUID_V4.test(r.parent_record_id))) {
    errors.push('parent_record_id');
  }
  if (r.prev_hash !== null && (typeof r.prev_hash !== 'string' || !HEX64.test(r.prev_hash))) errors.push('prev_hash');
  const d = r.action_detail;
  if (!d || typeof d !== 'object' || Array.isArray(d)) errors.push('action_detail');
  else if (r.action_type === 'decision' && (typeof d.decision_type !== 'string' || !d.decision_type)) {
    errors.push('decision_type');
  }
  if ('signature' in r) errors.push('signature');
  if ('cost_estimate' in r) errors.push('cost_estimate');
  let size = 0;
  try { size = Buffer.byteLength(JSON.stringify(r), 'utf8'); } catch (e) { errors.push('size'); }
  if (size > 256 * 1024) errors.push('size');
  return errors;
}

module.exports = {
  AAT_DRAFT, AAT_DRAFT_DATE, AAT_DRAFT_URL,
  ACTION_TYPES, OUTCOMES, PHASES, TRUST,
  uuidV4From, aatRecord, exportAat, validateAatRecord,
};
