'use strict';
/**
 * In-memory state for the authorization server.
 *
 * A production AS keeps all of this in a database or Redis, but the shapes
 * are the same. What matters for learning is *what has to be remembered*:
 *
 *   sessions   - the browser is logged in to the IdP (this is what makes SSO work)
 *   requests   - an /authorize call parked while the user logs in and consents
 *   codes      - one-time authorization codes, each bound to a PKCE challenge
 *   refresh    - refresh tokens, grouped into families so reuse can be detected
 *   grants     - "alice already said yes to these scopes for this app"
 *   revoked    - access-token ids (jti) killed before their exp
 */
const { randomId, randomToken, sha256Hex } = require('../shared/jose');

const now = () => Math.floor(Date.now() / 1000);

class Store {
  constructor() {
    this.sessions = new Map();
    this.requests = new Map();
    this.codes = new Map();
    this.refreshTokens = new Map();
    this.grants = new Map();
    this.revokedJti = new Set();
    this.issuedAccessTokens = new Map();
  }

  /* ---------------- IdP browser sessions ---------------- */
  createSession(sub, ttlSec) {
    const id = randomToken(32);
    this.sessions.set(id, { id, sub, authTime: now(), expiresAt: now() + ttlSec });
    return id;
  }
  getSession(id) {
    const session = id && this.sessions.get(id);
    if (!session) return null;
    if (session.expiresAt < now()) { this.sessions.delete(id); return null; }
    return session;
  }
  endSession(id) { return this.sessions.delete(id); }

  /* ---------------- Parked /authorize requests ---------------- */
  createRequest(params) {
    const id = randomId(16);
    this.requests.set(id, { id, createdAt: now(), ...params });
    return id;
  }
  getRequest(id) { return (id && this.requests.get(id)) || null; }
  deleteRequest(id) { this.requests.delete(id); }

  /* ---------------- Authorization codes ---------------- */
  /**
   * The code is a random opaque string. Everything the token endpoint will
   * need later is stored *server-side* against it -- especially the PKCE
   * challenge, the redirect_uri and the client_id, because all three must be
   * proven again when the code is redeemed.
   */
  createCode(payload, ttlSec) {
    const code = randomToken(32);
    this.codes.set(code, {
      code,
      issuedAt: now(),
      expiresAt: now() + ttlSec,
      used: false,
      usedAt: null,
      ...payload,
    });
    return code;
  }
  getCode(code) { return (code && this.codes.get(code)) || null; }
  markCodeUsed(code) {
    const entry = this.codes.get(code);
    if (entry) { entry.used = true; entry.usedAt = now(); }
    return entry;
  }
  deleteCode(code) { this.codes.delete(code); }

  /* ---------------- Refresh tokens ---------------- */
  /**
   * Refresh tokens are stored hashed: a database leak then yields nothing
   * usable, exactly as with passwords. `familyId` links every rotation of
   * the same original grant so that reuse can nuke the whole chain.
   */
  createRefreshToken({ clientId, sub, scope, familyId, generation = 1, ttlSec, audience }) {
    const token = randomToken(32);
    const family = familyId || randomId(8);
    this.refreshTokens.set(sha256Hex(token), {
      familyId: family,
      generation,
      clientId,
      sub,
      scope,
      audience,
      issuedAt: now(),
      expiresAt: now() + ttlSec,
      used: false,
      revoked: false,
    });
    return { token, familyId: family, generation };
  }
  getRefreshToken(token) {
    if (!token) return null;
    const hash = sha256Hex(token);
    const entry = this.refreshTokens.get(hash);
    return entry ? { hash, ...entry } : null;
  }
  updateRefreshToken(hash, patch) {
    const entry = this.refreshTokens.get(hash);
    if (entry) Object.assign(entry, patch);
    return entry;
  }
  /** Kill every token descended from the same original authorization. */
  revokeFamily(familyId) {
    let count = 0;
    for (const entry of this.refreshTokens.values()) {
      if (entry.familyId === familyId && !entry.revoked) { entry.revoked = true; count += 1; }
    }
    for (const [jti, meta] of this.issuedAccessTokens) {
      if (meta.familyId === familyId) this.revokedJti.add(jti);
    }
    return count;
  }
  revokeForClientAndUser(clientId, sub) {
    let count = 0;
    for (const entry of this.refreshTokens.values()) {
      if (entry.clientId === clientId && entry.sub === sub && !entry.revoked) { entry.revoked = true; count += 1; }
    }
    return count;
  }

  /* ---------------- Access-token bookkeeping ---------------- */
  recordAccessToken(jti, meta) { this.issuedAccessTokens.set(jti, { ...meta, issuedAt: now() }); }
  revokeJti(jti) { this.revokedJti.add(jti); }
  isJtiRevoked(jti) { return this.revokedJti.has(jti); }

  /* ---------------- Remembered consent ---------------- */
  grantKey(clientId, sub) { return `${clientId}::${sub}`; }
  rememberGrant(clientId, sub, scopes) {
    const key = this.grantKey(clientId, sub);
    const existing = this.grants.get(key) || { clientId, sub, scopes: [] };
    existing.scopes = [...new Set([...existing.scopes, ...scopes])];
    existing.updatedAt = now();
    this.grants.set(key, existing);
    return existing;
  }
  getGrant(clientId, sub) { return this.grants.get(this.grantKey(clientId, sub)) || null; }
  forgetGrant(clientId, sub) { return this.grants.delete(this.grantKey(clientId, sub)); }

  /** Snapshot for the admin/state page -- secrets are never included. */
  snapshot() {
    return {
      sessions: [...this.sessions.values()].map((s) => ({ sub: s.sub, authTime: s.authTime, expiresIn: s.expiresAt - now() })),
      pendingRequests: [...this.requests.values()].map((r) => ({ id: r.id, client_id: r.client_id, scope: r.scope, stage: r.stage })),
      codes: [...this.codes.values()].map((c) => ({
        code: `${c.code.slice(0, 8)}...`, client_id: c.clientId, sub: c.sub, scope: c.scope,
        code_challenge: c.codeChallenge ? `${c.codeChallenge.slice(0, 12)}...` : null,
        code_challenge_method: c.codeChallengeMethod, used: c.used, expiresIn: c.expiresAt - now(),
      })),
      refreshTokens: [...this.refreshTokens.values()].map((r) => ({
        familyId: r.familyId, generation: r.generation, client_id: r.clientId, sub: r.sub,
        scope: r.scope, used: r.used, revoked: r.revoked, expiresIn: r.expiresAt - now(),
      })),
      grants: [...this.grants.values()],
      revokedAccessTokens: this.revokedJti.size,
    };
  }
  reset() {
    this.sessions.clear(); this.requests.clear(); this.codes.clear();
    this.refreshTokens.clear(); this.grants.clear(); this.revokedJti.clear();
    this.issuedAccessTokens.clear();
  }
}

module.exports = { Store, now };
