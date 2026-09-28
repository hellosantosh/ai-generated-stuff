'use strict';
// ===========================================================================
// LAB FILE 1 of 4 - crypto-lab.js
// PKCE and JWT primitives, on Node core modules only. No dependencies.
// ===========================================================================
const crypto = require('node:crypto');

// --- base64url: base64 with +/ -> -_ and the = padding removed --------------
const b64u = (buf) => Buffer.from(buf).toString('base64url');
const unb64u = (str) => Buffer.from(str, 'base64url');

// --- PKCE (RFC 7636) -------------------------------------------------------

// The verifier is the secret: 32 random bytes, base64url -> 43 characters.
const createVerifier = () => crypto.randomBytes(32).toString('base64url');

// challenge = BASE64URL(SHA256(ASCII(verifier)))  <- hash the TEXT, not bytes
const challengeS256 = (verifier) =>
  crypto.createHash('sha256').update(verifier, 'ascii').digest('base64url');

// Show every step of the transform, for Lab 2.
function explainPkce(verifier) {
  const digest = crypto.createHash('sha256').update(verifier, 'ascii').digest();
  return {
    code_verifier: verifier,
    length: verifier.length,
    sha256_hex: digest.toString('hex'),
    sha256_base64: digest.toString('base64'),      // note the trailing '='
    code_challenge: digest.toString('base64url'),  // the same, url-safe
  };
}

// The authorization server's check. Constant-time: a comparison that returns
// early on the first differing byte leaks the stored challenge.
function verifyPkce(verifier, challenge, method) {
  if (!verifier) return { ok: false, reason: 'missing_code_verifier' };
  if (!/^[A-Za-z0-9\-._~]{43,128}$/.test(verifier))
    return { ok: false, reason: 'malformed_code_verifier' };
  const computed = method === 'plain' ? verifier : challengeS256(verifier);
  const a = Buffer.from(computed), b = Buffer.from(challenge || '');
  const ok = a.length === b.length && crypto.timingSafeEqual(a, b);
  return ok ? { ok: true, computed } : { ok: false, reason: 'pkce_mismatch', computed };
}

// --- Keys and JWTs ---------------------------------------------------------

// The key id is a SHA-256 over the key's own material (RFC 7638 thumbprint),
// so new key material always announces itself with a new kid. That is what
// lets the API cache public keys safely. Restarting this server rotates keys.
function generateKey() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('rsa', {
    modulusLength: 2048,
    publicKeyEncoding: { type: 'spki', format: 'pem' },
    privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
  });
  const jwk = crypto.createPublicKey(publicKey).export({ format: 'jwk' });
  const thumb = crypto.createHash('sha256')
    .update(JSON.stringify({ e: jwk.e, kty: jwk.kty, n: jwk.n }))
    .digest('base64url');
  const kid = 'lab-' + thumb.slice(0, 16);
  return { kid, privateKey, publicJwk: { ...jwk, alg: 'RS256', use: 'sig', kid } };
}

// A JWT is three base64url segments joined by dots: header.payload.signature.
function signJwt(payload, key, typ) {
  const header = { alg: 'RS256', typ: typ || 'JWT', kid: key.kid };
  const input = b64u(JSON.stringify(header)) + '.' + b64u(JSON.stringify(payload));
  const sig = crypto.sign('RSA-SHA256', Buffer.from(input), key.privateKey);
  return input + '.' + sig.toString('base64url');
}

// Decode WITHOUT verifying. For display only - never trust this output.
function decodeJwt(token) {
  const p = String(token || '').split('.');
  if (p.length !== 3) return null;
  try {
    return {
      header: JSON.parse(unb64u(p[0]).toString('utf8')),
      payload: JSON.parse(unb64u(p[1]).toString('utf8')),
      signingInput: p[0] + '.' + p[1],
      signature: p[2],
    };
  } catch { return null; }
}

// Verify the way a resource server must: signature first, then every claim
// that scopes the token to THIS api at THIS moment. See Chapter 11.
function verifyJwt(token, { getKey, issuer, audience, requiredTyp, skew = 5 }) {
  const d = decodeJwt(token);
  if (!d) return { valid: false, reason: 'malformed_token' };
  // Never let the token choose its own algorithm: that is how alg:none and
  // RS256->HS256 confusion attacks start.
  if (d.header.alg !== 'RS256') return { valid: false, reason: 'unsupported_alg' };
  if (requiredTyp && d.header.typ !== requiredTyp)
    return { valid: false, reason: 'unexpected_typ' };
  const key = getKey(d.header.kid);
  if (!key) return { valid: false, reason: 'unknown_kid' };
  const ok = crypto.verify('RSA-SHA256', Buffer.from(d.signingInput),
                           key, unb64u(d.signature));
  if (!ok) return { valid: false, reason: 'bad_signature' };

  const now = Math.floor(Date.now() / 1000);
  const p = d.payload;
  if (issuer && p.iss !== issuer) return { valid: false, reason: 'issuer_mismatch' };
  if (audience && ![].concat(p.aud || []).includes(audience))
    return { valid: false, reason: 'audience_mismatch' };
  if (typeof p.exp === 'number' && now > p.exp + skew)
    return { valid: false, reason: 'token_expired' };
  return { valid: true, payload: p, header: d.header };
}

// --- odds and ends ---------------------------------------------------------
const randomToken = (n = 32) => crypto.randomBytes(n).toString('base64url');
const randomId = (n = 8) => crypto.randomBytes(n).toString('hex');
const sha256hex = (v) => crypto.createHash('sha256').update(v).digest('hex');

function safeEqual(a, b) {
  const x = Buffer.from(String(a ?? '')), y = Buffer.from(String(b ?? ''));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

module.exports = {
  b64u, unb64u, createVerifier, challengeS256, explainPkce, verifyPkce,
  generateKey, signJwt, decodeJwt, verifyJwt,
  randomToken, randomId, sha256hex, safeEqual,
};
