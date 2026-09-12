'use strict';
/**
 * JWT / JWS / JWKS and PKCE primitives, written on `node:crypto` alone.
 *
 * Everything here is intentionally explicit: signing a JWT is three
 * base64url segments joined by dots, and verifying one is the same three
 * segments checked in reverse. Read `signJwt` and `verifyJwt` together and
 * you know what a JWT actually is.
 */
const crypto = require('node:crypto');

const b64url = (input) => Buffer.from(input).toString('base64url');
const b64urlJson = (obj) => b64url(JSON.stringify(obj));
const fromB64url = (str) => Buffer.from(str, 'base64url');

/**
 * The RFC 7638 JWK thumbprint: a SHA-256 over the key's required members in
 * lexicographic order. Using it as the `kid` means the key id is a function
 * of the key material, so new key material always announces itself with a
 * new id -- which is exactly what lets a resource server cache keys safely.
 */
function jwkThumbprint(publicKeyPem) {
  const jwk = crypto.createPublicKey(publicKeyPem).export({ format: 'jwk' });
  const canonical = JSON.stringify({ e: jwk.e, kty: jwk.kty, n: jwk.n });
  return crypto.createHash('sha256').update(canonical).digest('base64url');
}

/** A 2048-bit RSA key pair for RS256 signing, generated at boot. */
function generateSigningKey(kidPrefix = 'sim') {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('rsa', {
    modulusLength: 2048,
    publicKeyEncoding: { type: 'spki', format: 'pem' },
    privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
  });
  // Restarting this server therefore *rotates* the key, kid and all. A cache
  // keyed on kid sees a brand-new id and refetches, instead of silently
  // verifying against a key that no longer exists.
  const kid = `${kidPrefix}-${jwkThumbprint(publicKey).slice(0, 16)}`;
  return { kid, alg: 'RS256', publicKeyPem: publicKey, privateKeyPem: privateKey };
}

/** Export an RSA public key as a JWK, the format the JWKS endpoint serves. */
function publicKeyToJwk(publicKeyPem, kid) {
  const jwk = crypto.createPublicKey(publicKeyPem).export({ format: 'jwk' });
  return { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: 'RS256', use: 'sig', kid };
}

function jwkToPublicKey(jwk) {
  return crypto.createPublicKey({ key: jwk, format: 'jwk' });
}

/**
 * Sign a JWT: base64url(header) + "." + base64url(payload), signed, then
 * the signature appended as a third base64url segment.
 */
function signJwt(payload, key, extraHeader = {}) {
  const header = { alg: 'RS256', typ: 'JWT', kid: key.kid, ...extraHeader };
  const signingInput = `${b64urlJson(header)}.${b64urlJson(payload)}`;
  const signature = crypto.sign('RSA-SHA256', Buffer.from(signingInput), key.privateKeyPem);
  return `${signingInput}.${signature.toString('base64url')}`;
}

/** Decode without verifying -- for display only. Never trust this output. */
function decodeJwt(token) {
  const parts = String(token || '').split('.');
  if (parts.length !== 3) return null;
  try {
    return {
      header: JSON.parse(fromB64url(parts[0]).toString('utf8')),
      payload: JSON.parse(fromB64url(parts[1]).toString('utf8')),
      signature: parts[2],
      signingInput: `${parts[0]}.${parts[1]}`,
    };
  } catch {
    return null;
  }
}

/**
 * Verify a JWT the way a resource server must: signature first, then every
 * claim that scopes the token to *this* API at *this* moment.
 */
function verifyJwt(token, { getKey, issuer, audience, clockSkewSec = 5, requiredTyp } = {}) {
  const decoded = decodeJwt(token);
  if (!decoded) return { valid: false, reason: 'malformed_token', detail: 'Token is not three base64url segments separated by dots.' };
  const { header, payload, signature, signingInput } = decoded;

  // Never let the token choose its own algorithm: `alg: none` and HMAC
  // confusion attacks both start with a server that trusts this header.
  if (header.alg !== 'RS256') return { valid: false, reason: 'unsupported_alg', detail: `Refusing alg="${header.alg}"; this API only accepts RS256.`, decoded };
  if (requiredTyp && header.typ !== requiredTyp) return { valid: false, reason: 'unexpected_typ', detail: `Expected typ="${requiredTyp}" but got "${header.typ}".`, decoded };

  const key = getKey(header.kid);
  if (!key) return { valid: false, reason: 'unknown_kid', detail: `No published key matches kid="${header.kid}".`, decoded };

  const ok = crypto.verify('RSA-SHA256', Buffer.from(signingInput), key, fromB64url(signature));
  if (!ok) return { valid: false, reason: 'bad_signature', detail: 'The signature does not match the header+payload. The token was altered or signed by someone else.', decoded };

  const now = Math.floor(Date.now() / 1000);
  if (issuer && payload.iss !== issuer) return { valid: false, reason: 'issuer_mismatch', detail: `Expected iss="${issuer}" but token says "${payload.iss}".`, decoded };
  if (audience) {
    const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    if (!aud.includes(audience)) {
      return { valid: false, reason: 'audience_mismatch', detail: `This token was minted for ${JSON.stringify(payload.aud)}, not for "${audience}". A token is only valid at its intended audience.`, decoded };
    }
  }
  if (typeof payload.exp === 'number' && now > payload.exp + clockSkewSec) {
    return { valid: false, reason: 'token_expired', detail: `Expired ${now - payload.exp}s ago (exp=${payload.exp}, now=${now}).`, decoded };
  }
  if (typeof payload.nbf === 'number' && now + clockSkewSec < payload.nbf) {
    return { valid: false, reason: 'token_not_yet_valid', detail: `Token is not valid until nbf=${payload.nbf}.`, decoded };
  }
  return { valid: true, header, payload, decoded };
}

/* ------------------------------------------------------------------ *
 * PKCE -- RFC 7636
 * ------------------------------------------------------------------ */

/**
 * The code verifier is a high-entropy random string, 43-128 characters of
 * the unreserved set [A-Z a-z 0-9 - . _ ~]. base64url of 32 random bytes
 * gives exactly 43 characters, which is the common choice.
 */
function createCodeVerifier(bytes = 32) {
  return crypto.randomBytes(bytes).toString('base64url');
}

/** challenge = BASE64URL(SHA256(ASCII(verifier))) */
function codeChallengeS256(verifier) {
  return crypto.createHash('sha256').update(verifier, 'ascii').digest('base64url');
}

/** Show the intermediate steps so learners can follow the transform. */
function explainCodeChallenge(verifier) {
  const digest = crypto.createHash('sha256').update(verifier, 'ascii').digest();
  return {
    code_verifier: verifier,
    verifier_length: verifier.length,
    sha256_hex: digest.toString('hex'),
    sha256_base64: digest.toString('base64'),
    code_challenge: digest.toString('base64url'),
    code_challenge_method: 'S256',
    note: 'base64url is base64 with + -> -, / -> _ and the = padding removed.',
  };
}

/**
 * Compare a presented verifier against a stored challenge in constant time.
 * Timing-safe comparison keeps an attacker from discovering the challenge
 * one byte at a time by measuring how long the rejection took.
 */
function verifyPkce({ codeVerifier, codeChallenge, codeChallengeMethod }) {
  if (!codeVerifier) {
    return { ok: false, reason: 'missing_code_verifier', detail: 'The token request carried no code_verifier, so the client cannot prove it is the same app that started the flow.' };
  }
  if (!/^[A-Za-z0-9\-._~]{43,128}$/.test(codeVerifier)) {
    return { ok: false, reason: 'malformed_code_verifier', detail: 'RFC 7636 requires 43-128 characters from the unreserved set [A-Za-z0-9-._~].' };
  }
  const computed = codeChallengeMethod === 'plain' ? codeVerifier : codeChallengeS256(codeVerifier);
  const a = Buffer.from(computed);
  const b = Buffer.from(codeChallenge || '');
  const ok = a.length === b.length && crypto.timingSafeEqual(a, b);
  return ok
    ? { ok: true, computed }
    : { ok: false, reason: 'pkce_mismatch', computed, detail: `Computed challenge "${computed}" does not equal the stored challenge "${codeChallenge}". Whoever sent this verifier did not start the flow.` };
}

/* ------------------------------------------------------------------ *
 * Random identifiers and opaque tokens
 * ------------------------------------------------------------------ */
const randomId = (bytes = 16) => crypto.randomBytes(bytes).toString('hex');
const randomToken = (bytes = 32) => crypto.randomBytes(bytes).toString('base64url');
const sha256Hex = (value) => crypto.createHash('sha256').update(value).digest('hex');

/** Compare two secrets without leaking their contents through timing. */
function safeEqual(a, b) {
  const bufA = Buffer.from(String(a ?? ''));
  const bufB = Buffer.from(String(b ?? ''));
  if (bufA.length !== bufB.length) return false;
  return crypto.timingSafeEqual(bufA, bufB);
}

module.exports = {
  b64url, b64urlJson, fromB64url,
  generateSigningKey, publicKeyToJwk, jwkToPublicKey, jwkThumbprint,
  signJwt, decodeJwt, verifyJwt,
  createCodeVerifier, codeChallengeS256, explainCodeChallenge, verifyPkce,
  randomId, randomToken, sha256Hex, safeEqual,
};
