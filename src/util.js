'use strict';

// Small dependency-free helpers shared by the post station and the reply rooms.

const crypto = require('node:crypto');

let counter = 0;

function uid(prefix = '') {
  counter += 1;
  // random part makes values unguessable; the counter only guards against the
  // astronomically unlikely collision inside one process.
  return `${prefix}${crypto.randomBytes(9).toString('hex')}${counter.toString(36)}`;
}

function token(prefix = '') {
  return `${prefix}${crypto.randomBytes(24).toString('hex')}`;
}

function sha256(value) {
  return crypto.createHash('sha256').update(String(value)).digest('hex');
}

function hmac(key, message) {
  return crypto.createHmac('sha256', key).update(String(message)).digest('hex');
}

// Deterministic JSON so a certificate signature is stable regardless of
// insertion order.
function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
}

const DAY_MS = 24 * 60 * 60 * 1000;

class PostError extends Error {
  constructor(code, message, detail) {
    super(message || code);
    this.name = 'PostError';
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function invariant(condition, code, message, detail) {
  if (!condition) throw new PostError(code, message, detail);
}

module.exports = {
  uid,
  token,
  sha256,
  hmac,
  canonical,
  DAY_MS,
  PostError,
  invariant,
};
