'use strict';
// ===========================================================================
// LAB FILE 2 of 4 - authz-server.js        run:  node authz-server.js
// A complete OAuth 2.1 authorization server + identity provider.
// Implements: RFC 6749 (as narrowed by 2.1), 7636 PKCE, 8414 discovery,
//             7662 introspection, 7009 revocation, 9068 at+jwt, 9700 BCP.
// ===========================================================================
const http = require('node:http');
const C = require('./crypto-lab');

const PORT = 7000;
const ISSUER = 'http://localhost:7000';   // browser-facing identity; goes in `iss`
const API = 'http://localhost:7010';      // the audience we mint tokens for

// Grant types are URNs, not short names, once you leave the core spec.
const DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code';
const EXCHANGE_GRANT = 'urn:ietf:params:oauth:grant-type:token-exchange';
const ACCESS_TOKEN_TYPE = 'urn:ietf:params:oauth:token-type:access_token';

// --- who can log in --------------------------------------------------------
const USERS = {
  alice: { sub: 'u-1001', password: 'wonderland', name: 'Alice Liddell',
           email: 'alice@lab.test',
           can: ['openid', 'profile', 'email', 'accounts:read', 'payments:write'] },
  // bob deliberately cannot delegate payments:write - see Lab 6.
  bob:   { sub: 'u-1002', password: 'builder', name: 'Bob Builder',
           email: 'bob@lab.test',
           can: ['openid', 'profile', 'email', 'accounts:read'] },
};
const userBySub = (sub) => Object.values(USERS).find((u) => u.sub === sub);
const usernameOf = (sub) =>
  Object.keys(USERS).find((k) => USERS[k].sub === sub) || null;

// --- which apps are registered --------------------------------------------
const CLIENTS = {
  // A server-side app CAN keep a secret, so it authenticates to /token.
  'demo-web-app': { secret: 'web-app-secret', type: 'confidential',
    redirect_uris: ['http://localhost:7020/callback'],
    grants: ['authorization_code', 'refresh_token'],
    scopes: ['openid', 'profile', 'email', 'accounts:read', 'payments:write'] },
  // A browser or mobile app CANNOT. PKCE is its only proof of identity.
  'demo-spa': { secret: null, type: 'public',
    redirect_uris: ['http://localhost:7020/spa-callback'],
    grants: ['authorization_code', 'refresh_token'],
    scopes: ['openid', 'profile', 'accounts:read'] },
  // No user involved at all: the app acts as itself.
  'demo-service': { secret: 'service-secret', type: 'confidential',
    redirect_uris: [], grants: ['client_credentials'],
    scopes: ['accounts:read', 'profile'] },

  // --- agentic access (Chapter 15) ---------------------------------------
  // An autonomous agent has no browser, so it cannot use a redirect. It asks
  // for a code the human types in elsewhere: the device grant, RFC 8628.
  // Public, because an agent binary or container cannot keep a secret either.
  'demo-agent': { secret: null, type: 'public', redirect_uris: [],
    grants: [DEVICE_GRANT, 'refresh_token'],
    scopes: ['openid', 'profile', 'accounts:read', 'payments:write'] },

  // The service an agent calls, which must then call the API *as the user*.
  // It swaps the user's token for a downstream one (RFC 8693) that records
  // who is acting, so the API can see the whole chain.
  'demo-orchestrator': { secret: 'orchestrator-secret', type: 'confidential',
    redirect_uris: [], grants: [EXCHANGE_GRANT, 'client_credentials'],
    scopes: ['accounts:read', 'payments:write'] },
};

const SCOPES = ['openid', 'profile', 'email', 'accounts:read', 'payments:write'];

// --- the safety rules. Every one is ON in a correct OAuth 2.1 server. ------
// Lab 3 turns require_pkce off and watches a stolen code start working.
const policy = {
  require_pkce: true,        // PKCE for every authorization_code request
  exact_redirect_uri: true,  // string equality, no wildcards, no prefixes
  single_use_codes: true,    // a replayed code is revoked, not honoured
  rotate_refresh: true,      // a new refresh token on every refresh
  detect_reuse: true,        // a replayed refresh token kills the whole family
};

const TTL = { code: 60, access: 300, refresh: 3600, session: 1800, device: 300 };

// --- server-side state (a real AS keeps this in a database) ---------------
const sessions = new Map();   // browser is logged in to the IdP
const requests = new Map();   // an /authorize call parked during login
const codes = new Map();      // one-time authorization codes
const refresh = new Map();    // sha256(token) -> record, grouped into families
const revoked = new Set();    // access-token ids killed before exp
const devices = new Map();    // device_code -> a pending agent authorization

const KEY = C.generateKey();
const now = () => Math.floor(Date.now() / 1000);
const splitScope = (s) => String(s || '').trim().split(/\s+/).filter(Boolean);

// --- tiny http helpers ----------------------------------------------------
function readBody(req) {
  return new Promise((resolve) => {
    let raw = '';
    req.on('data', (c) => { raw += c; if (raw.length > 1e5) req.destroy(); });
    req.on('end', () => resolve(new URLSearchParams(raw)));
  });
}
const cookies = (req) => Object.fromEntries((req.headers.cookie || '')
  .split(';').map((p) => p.trim().split('=')).filter((p) => p[0]));

function json(res, status, obj) {
  res.writeHead(status, { 'content-type': 'application/json',
                          'cache-control': 'no-store' });  // these are credentials
  res.end(JSON.stringify(obj, null, 2));
}
const html = (res, body, status = 200) =>
  (res.writeHead(status, { 'content-type': 'text/html; charset=utf-8' }),
   res.end('<!doctype html><meta charset=utf-8>' +
     '<body style="font:15px/1.5 system-ui;max-width:34em;margin:3em auto">' +
     body));
const redirect = (res, location) => (res.writeHead(302, { location }), res.end());
const esc = (s) => String(s).replace(/[<>&"]/g, (c) =>
  ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

// Deliver an error to the client by redirect. Only ever called AFTER the
// client_id and redirect_uri are both validated - otherwise this endpoint
// would be an open redirector.
function redirectError(res, uri, error, description, state) {
  const u = new URL(uri);
  u.searchParams.set('error', error);
  if (description) u.searchParams.set('error_description', description);
  if (state) u.searchParams.set('state', state);
  log('reject', error + ': ' + description);
  return redirect(res, u.toString());
}
const log = (tag, msg) => console.log('  [as] ' + tag.padEnd(7) + msg);

// --- client authentication ------------------------------------------------
function authenticateClient(req, body) {
  const auth = req.headers.authorization || '';
  let id, secret;
  if (auth.toLowerCase().startsWith('basic ')) {
    const raw = Buffer.from(auth.slice(6), 'base64').toString('utf8');
    const i = raw.indexOf(':');
    id = decodeURIComponent(raw.slice(0, i));
    secret = decodeURIComponent(raw.slice(i + 1));
  } else { id = body.get('client_id'); secret = body.get('client_secret'); }

  const client = CLIENTS[id];
  if (!client) return { error: 'invalid_client', description: 'Unknown client_id.' };
  if (client.type === 'confidential') {
    if (!C.safeEqual(secret, client.secret))
      return { error: 'invalid_client', description: 'Client authentication failed.' };
    log('ok', 'client authenticated: ' + id);
  } else {
    // A public client is IDENTIFIED, never authenticated. PKCE carries the proof.
    log('info', 'public client identified: ' + id + ' (PKCE carries the proof)');
  }
  return { id, client };
}

// --- token minting --------------------------------------------------------
function issueAccessToken(clientId, sub, scope, familyId, audience, extra) {
  const iat = now(), jti = C.randomId(12);
  const claims = {
    iss: ISSUER, sub, aud: audience || API, client_id: clientId,
    scope: scope.join(' '), jti, iat, nbf: iat, exp: iat + TTL.access,
  };
  // `act` records WHO is acting on the subject's behalf (RFC 8693 4.1). The
  // API can then log and authorize the whole chain, not just the end user.
  if (extra && extra.act) claims.act = extra.act;
  // `authorization_details` carries structured, per-request limits that a
  // coarse scope string cannot express (RFC 9396). See Chapter 15.
  if (extra && extra.details) claims.authorization_details = extra.details;
  const token = C.signJwt(claims, KEY, 'at+jwt');
  return { token, jti, familyId };
}

// Parse and sanity-check an authorization_details value (RFC 9396).
function parseDetails(raw) {
  if (!raw) return null;
  let d;
  try { d = JSON.parse(raw); } catch { return { error: 'not JSON' }; }
  if (!Array.isArray(d)) return { error: 'must be a JSON array' };
  for (const entry of d) {
    if (!entry || typeof entry !== 'object' || !entry.type)
      return { error: 'each entry needs a "type"' };
  }
  return { value: d };
}

// A short code a human can read off a screen and type somewhere else.
// The alphabet omits characters people confuse: 0/O, 1/I, etc.
function makeUserCode() {
  const AB = 'BCDFGHJKLMNPQRSTVWXZ';
  let out = '';
  for (let i = 0; i < 8; i++) out += AB[Math.floor(Math.random() * AB.length)];
  return out.slice(0, 4) + '-' + out.slice(4);
}

function issueRefreshToken(clientId, sub, scope, familyId, generation) {
  const token = C.randomToken(32);
  // Stored hashed, like a password: a database leak then yields nothing usable.
  refresh.set(C.sha256hex(token), { clientId, sub, scope, familyId, generation,
    used: false, revoked: false, expiresAt: now() + TTL.refresh });
  return token;
}

function revokeFamily(familyId) {
  let n = 0;
  for (const r of refresh.values())
    if (r.familyId === familyId && !r.revoked) { r.revoked = true; n++; }
  return n;
}

// ===========================================================================
// The server
// ===========================================================================
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, ISSUER);
  const q = url.searchParams;
  const path = url.pathname;
  const body = req.method === 'POST' ? await readBody(req) : new URLSearchParams();

  // ---------------- discovery + keys (RFC 8414) ----------------
  if (path === '/.well-known/oauth-authorization-server') return json(res, 200, {
    issuer: ISSUER,
    authorization_endpoint: ISSUER + '/authorize',
    token_endpoint: ISSUER + '/token',
    // RFC 8628 requires advertising this, or an agent cannot discover it.
    device_authorization_endpoint: ISSUER + '/device_authorization',
    // The OpenID Connect half of this server (Chapter 8).
    userinfo_endpoint: ISSUER + '/userinfo',
    subject_types_supported: ['public'],
    id_token_signing_alg_values_supported: ['RS256'],
    claims_supported: ['sub', 'name', 'preferred_username', 'email',
                       'email_verified', 'auth_time', 'nonce'],
    jwks_uri: ISSUER + '/jwks.json',
    introspection_endpoint: ISSUER + '/introspect',
    revocation_endpoint: ISSUER + '/revoke',
    scopes_supported: SCOPES,
    // OAuth 2.1: `code` only. The implicit grant is gone, so no response type
    // can return a token in the front channel.
    response_types_supported: ['code'],
    grant_types_supported: ['authorization_code', 'refresh_token',
                            'client_credentials', DEVICE_GRANT, EXCHANGE_GRANT],
    // Tell clients we understand structured grants (RFC 9396), and which
    // kinds. An agent can then ask for limits instead of broad scopes.
    authorization_details_types_supported: ['payment_initiation'],
    code_challenge_methods_supported: ['S256'],   // never offer `plain`
    token_endpoint_auth_methods_supported: ['client_secret_basic', 'none'],
  });

  if (path === '/jwks.json') return json(res, 200, { keys: [KEY.publicJwk] });
  if (path === '/healthz')
    return json(res, 200, { ok: true, issuer: ISSUER, kid: KEY.kid });

  // ---------------- the lab switchboard ----------------
  if (path === '/policy' && req.method === 'GET') return json(res, 200, policy);
  if (path === '/policy' && req.method === 'POST') {
    const key = body.get('key'), value = body.get('value') === 'true';
    if (!(key in policy)) return json(res, 400, { error: 'unknown_policy_key' });
    policy[key] = value;
    log('policy', key + ' = ' + value + (value ? '' : '   << now running UNSAFELY'));
    return json(res, 200, policy);
  }

  // ---------------- /authorize : the FRONT CHANNEL ----------------
  // Runs in the browser. Everything here is visible in the URL bar, history,
  // logs and Referer headers, so nothing secret may travel on it.
  if (path === '/authorize') {
    const client = CLIENTS[q.get('client_id')];
    // Step 1: identify the client. Not yet trusted - just recognised.
    if (!client) return html(res,
      '<h2>invalid_client</h2><p>Unknown client_id. The authorization server will '
      + 'not redirect an error for an unknown client, because it has no registered '
      + 'URI it can trust.</p>', 400);

    // Step 2: validate redirect_uri BEFORE trusting it.
    const uri = q.get('redirect_uri') || '';
    const match = policy.exact_redirect_uri
      ? client.redirect_uris.includes(uri)
      : client.redirect_uris.some((r) => uri.startsWith(r.split('/callback')[0]));
    if (!match) return html(res,
      '<h2>invalid_request</h2><p>redirect_uri <code>' + esc(uri) + '</code> is not '
      + 'registered. This check is what stops an attacker having your authorization '
      + 'code delivered to their own server.</p>', 400);
    if (!policy.exact_redirect_uri)
      log('warn', 'redirect_uri matched by PREFIX (unsafe)');

    const state = q.get('state');

    // Step 3: response_type. OAuth 2.1 means `code`, full stop.
    if (q.get('response_type') !== 'code') return redirectError(res, uri,
      'unsupported_response_type',
      'OAuth 2.1 supports only response_type=code. The implicit grant was removed '
      + 'because it delivered access tokens through the browser URL.', state);

    // Step 4: PKCE. The defining requirement of OAuth 2.1.
    const challenge = q.get('code_challenge');
    const method = q.get('code_challenge_method');
    if (!challenge) {
      if (policy.require_pkce) return redirectError(res, uri, 'invalid_request',
        'code_challenge is required. OAuth 2.1 mandates PKCE for every '
        + 'authorization code request, for public and confidential clients alike.',
        state);
      log('warn', 'PROCEEDING WITHOUT PKCE - this code will be stealable');
    } else {
      if (!method) return redirectError(res, uri, 'invalid_request',
        'Send code_challenge_method=S256 explicitly. If omitted, RFC 7636 '
        + 'defaults to "plain", which offers no protection.', state);
      if (method !== 'S256') return redirectError(res, uri, 'invalid_request',
        'code_challenge_method must be S256. With "plain" the challenge IS the '
        + 'verifier, so anyone who sees the request can redeem the code.', state);
      if (!/^[A-Za-z0-9\-_]{43}$/.test(challenge)) return redirectError(res, uri,
        'invalid_request', 'An S256 code_challenge is 43 base64url characters. '
        + 'Did you send the verifier by mistake?', state);
      log('ok', 'PKCE challenge stored: ' + challenge.slice(0, 16) + '...');
    }

    // Step 5: scope, capped by what this client is registered for.
    const wanted = splitScope(q.get('scope'));
    if (!wanted.length) return redirectError(res, uri, 'invalid_scope',
      'No scope requested.', state);
    const bad = wanted.filter((s) => !client.scopes.includes(s));
    if (bad.length) return redirectError(res, uri, 'invalid_scope',
      'Client is not registered for: ' + bad.join(' '), state);

    // Step 6: park the request and hand over to the IdP.
    const rid = C.randomId(16);
    requests.set(rid, { clientId: q.get('client_id'), uri, scope: wanted, state,
      nonce: q.get('nonce'), challenge, method });
    return resume(res, rid, cookies(req).lab_session);
  }

  // ---------------- IdP: authentication ----------------
  if (path === '/login' && req.method === 'POST') {
    const rid = body.get('request_id'), r = requests.get(rid);
    if (!r) return html(res, '<h2>Expired</h2><p>Start again.</p>', 400);
    const user = USERS[String(body.get('username') || '').toLowerCase()];
    // One generic message for both cases: telling them apart is a free
    // user-enumeration oracle for an attacker.
    if (!user || !C.safeEqual(body.get('password'), user.password)) {
      log('reject', 'authentication failed');
      return loginPage(res, rid, r, 'That username and password is not correct.');
    }
    const sid = C.randomToken(24);
    sessions.set(sid, { sub: user.sub, authTime: now(),
                        expiresAt: now() + TTL.session });
    log('ok', 'user authenticated: ' + user.name);
    res.setHeader('set-cookie', 'lab_session=' + sid
      + '; Path=/; HttpOnly; SameSite=Lax; Max-Age=' + TTL.session);
    return resume(res, rid, sid);
  }

  // ---------------- OAuth: consent, then the code ----------------
  if (path === '/consent' && req.method === 'POST') {
    const rid = body.get('request_id'), r = requests.get(rid);
    if (!r) return html(res, '<h2>Expired</h2>', 400);
    const s = sessions.get(cookies(req).lab_session);
    if (!s) return resume(res, rid, null);
    const user = userBySub(s.sub);

    if (body.get('action') !== 'allow') {
      requests.delete(rid);
      return redirectError(res, r.uri, 'access_denied',
        'The resource owner refused. Every client must handle this.', r.state);
    }
    // Approved = asked for AND the user holds it AND the user ticked it.
    const ticked = body.getAll('scope');
    const granted = r.scope.filter((x) =>
      user.can.includes(x) && (ticked.includes(x) || x === 'openid'));
    if (!granted.length) {
      requests.delete(rid);
      return redirectError(res, r.uri, 'access_denied', 'No scopes granted.', r.state);
    }
    if (granted.length !== r.scope.length)
      log('warn', 'granted scope is NARROWER than requested: ' + granted.join(' '));

    // Bind to the code everything /token must re-check: the client, the
    // redirect_uri, and the PKCE challenge. The code itself means nothing.
    const code = C.randomToken(32);
    codes.set(code, { clientId: r.clientId, sub: s.sub, scope: granted, uri: r.uri,
      challenge: r.challenge, method: r.method, nonce: r.nonce,
      authTime: s.authTime, used: false, expiresAt: now() + TTL.code });
    requests.delete(rid);
    log('ok', 'code issued: ' + code.slice(0, 12) + '...  scope=' + granted.join(' '));

    const out = new URL(r.uri);
    out.searchParams.set('code', code);
    if (r.state) out.searchParams.set('state', r.state);
    out.searchParams.set('iss', ISSUER);   // RFC 9207: mix-up defence
    return redirect(res, out.toString());
  }

  // ---------------- agentic access: the device grant (RFC 8628) ----------
  // An agent with no browser asks here first. It gets a code the HUMAN types
  // in somewhere else, on a device that does have a browser.
  if (path === '/device_authorization' && req.method === 'POST') {
    const auth = authenticateClient(req, body);
    if (auth.error) return json(res, 401, { error: auth.error });
    if (!auth.client.grants.includes(DEVICE_GRANT))
      return json(res, 400, { error: 'unauthorized_client' });

    const asked = splitScope(body.get('scope'));
    const bad = asked.filter((x) => !auth.client.scopes.includes(x));
    if (bad.length) return json(res, 400, { error: 'invalid_scope',
      error_description: 'Not registered for: ' + bad.join(' ') });

    // An agent may also ask for structured limits, not just scopes.
    const parsed = parseDetails(body.get('authorization_details'));
    if (parsed && parsed.error) return json(res, 400, {
      error: 'invalid_authorization_details', error_description: parsed.error });

    const deviceCode = C.randomToken(32), userCode = makeUserCode();
    devices.set(deviceCode, { userCode, clientId: auth.id, scope: asked,
      details: parsed ? parsed.value : null, status: 'pending', sub: null,
      lastPoll: 0, expiresAt: now() + TTL.device });
    log('ok', 'device authorization started, user_code=' + userCode);
    return json(res, 200, {
      device_code: deviceCode,
      user_code: userCode,
      verification_uri: ISSUER + '/device',
      verification_uri_complete: ISSUER + '/device?user_code=' + userCode,
      expires_in: TTL.device,
      interval: 5,          // the agent must not poll faster than this
    });
  }

  // The page the human opens. In a production server this would reuse the
  // normal session and consent screens; it is collapsed into one form here
  // so the lab stays readable.
  if (path === '/device' && req.method === 'GET') {
    const pre = q.get('user_code') || '';
    return html(res,
      '<h2>Authorize a device</h2><p>An application is asking to act on your '
      + 'behalf. Check the code shown on that device matches the one below, then '
      + 'sign in to approve it.</p>'
      + '<form method=POST action=/device>'
      + '<p><label>Code<br><input name=user_code value="' + esc(pre) + '"></label>'
      + '<p><label>Username<br><input name=username autofocus></label>'
      + '<p><label>Password<br><input name=password type=password></label>'
      + '<p><button name=action value=allow>Approve</button> '
      + '<button name=action value=deny>Deny</button></form>'
      + '<hr><p style="color:#666;font-size:13px">alice / wonderland</p>');
  }

  if (path === '/device' && req.method === 'POST') {
    const code = String(body.get('user_code') || '').trim().toUpperCase();
    let entry = null;
    for (const d of devices.values()) if (d.userCode === code) entry = d;
    if (!entry || entry.expiresAt < now())
      return html(res, '<h2>Unknown or expired code</h2>', 400);

    if (body.get('action') === 'deny') {
      entry.status = 'denied';
      log('warn', 'the human DENIED the device request');
      return html(res, '<h2>Denied</h2><p>You can close this page.</p>');
    }
    const user = USERS[String(body.get('username') || '').toLowerCase()];
    if (!user || !C.safeEqual(body.get('password'), user.password))
      return html(res, '<h2>Wrong username or password</h2>', 401);

    // The user may hold less than the agent asked for. Narrow it here.
    entry.scope = entry.scope.filter((x) => user.can.includes(x));
    entry.sub = user.sub;
    entry.status = 'approved';
    log('ok', 'device approved by ' + user.name + ' scope=' + entry.scope.join(' '));
    return html(res, '<h2>Approved</h2><p>The application can continue. You can '
      + 'close this page.</p>');
  }

  // ---------------- /token : the BACK CHANNEL ----------------
  if (path === '/token' && req.method === 'POST') {
    const grant = body.get('grant_type');
    log('step', 'POST /token grant_type=' + grant);

    if (grant === 'password' || grant === 'implicit') return json(res, 400, {
      error: 'unsupported_grant_type',
      error_description: 'Removed in OAuth 2.1. The password grant made the app '
        + 'handle the real password: no consent, no MFA, no federation. Use '
        + 'authorization_code + PKCE.' });

    const auth = authenticateClient(req, body);
    if (auth.error) {
      res.setHeader('www-authenticate', 'Basic realm="lab"');
      return json(res, 401,
        { error: auth.error, error_description: auth.description });
    }
    const { id: clientId, client } = auth;
    if (!client.grants.includes(grant)) return json(res, 400, {
      error: 'unauthorized_client',
      error_description: 'Client is not registered for grant_type=' + grant });

    // ===== authorization_code =====
    if (grant === 'authorization_code') {
      const code = body.get('code');
      const entry = codes.get(code);
      if (!entry) return json(res, 400, { error: 'invalid_grant',
        error_description: 'Unknown authorization code.' });

      // Replay detection. A code is a one-shot credential (RFC 9700 4.1.3).
      if (entry.used) {
        if (policy.single_use_codes) {
          const killed = entry.familyId ? revokeFamily(entry.familyId) : 0;
          log('reject', 'CODE REPLAY - revoked ' + killed + ' token(s) it produced');
          return json(res, 400, { error: 'invalid_grant',
            error_description: 'This code was already used. A replay means someone '
              + 'else may hold it, so every token derived from it is now revoked.' });
        }
        log('warn', 'CODE REPLAY ALLOWED - single_use_codes is off');
      }
      if (entry.expiresAt < now()) return json(res, 400, { error: 'invalid_grant',
        error_description: 'Code expired. Codes live ~' + TTL.code + 's because they '
          + 'cross the browser.' });
      if (entry.clientId !== clientId) {
        log('reject', 'code substitution blocked: issued to ' + entry.clientId);
        return json(res, 400, { error: 'invalid_grant',
          error_description: 'This code was issued to a different client.' });
      }
      if (entry.uri !== body.get('redirect_uri')) return json(res, 400, {
        error: 'invalid_grant',
        error_description: 'redirect_uri does not match the authorization request.' });

      // ===== the PKCE check: prove you started this flow =====
      if (entry.challenge) {
        const r = C.verifyPkce(body.get('code_verifier'),
                               entry.challenge, entry.method);
        if (!r.ok) {
          entry.used = true;   // burn it: something is wrong
          log('reject', 'PKCE FAILED (' + r.reason + ') expected='
            + entry.challenge.slice(0, 16) + '... got='
            + String(r.computed).slice(0, 16));
          return json(res, 400, { error: 'invalid_grant',
            error_description: 'PKCE check failed (' + r.reason + '). The hash of the '
              + 'presented code_verifier does not equal the stored code_challenge, so '
              + 'whoever sent this did not start the flow.' });
        }
        log('ok', 'PKCE verified: SHA256(verifier) == stored challenge');
      } else {
        log('warn', 'no PKCE binding - anyone holding this code could redeem it');
      }

      entry.used = true;
      const familyId = C.randomId(8);
      entry.familyId = familyId;
      const user = userBySub(entry.sub);
      const at = issueAccessToken(clientId, entry.sub, entry.scope, familyId);
      const out = { access_token: at.token, token_type: 'Bearer',
                    expires_in: TTL.access, scope: entry.scope.join(' ') };

      if (client.grants.includes('refresh_token'))
        out.refresh_token = issueRefreshToken(clientId, entry.sub, entry.scope,
                                              familyId, 1);
      if (entry.scope.includes('openid')) {
        // An ID token is for the CLIENT: aud is the client_id, not the API.
        const iat = now();
        out.id_token = C.signJwt({ iss: ISSUER, sub: user.sub, aud: clientId,
          iat, exp: iat + TTL.access, auth_time: entry.authTime,
          nonce: entry.nonce || undefined,
          name: entry.scope.includes('profile') ? user.name : undefined,
          email: entry.scope.includes('email') ? user.email : undefined }, KEY, 'JWT');
      }
      log('ok', 'code exchanged for tokens');
      return json(res, 200, out);
    }

    // ===== refresh_token =====
    if (grant === 'refresh_token') {
      const presented = body.get('refresh_token');
      const hash = C.sha256hex(String(presented || ''));
      const rec = refresh.get(hash);
      if (!rec) return json(res, 400, { error: 'invalid_grant',
        error_description: 'Unknown refresh token.' });
      if (rec.clientId !== clientId) return json(res, 400, { error: 'invalid_grant',
        error_description: 'This refresh token belongs to a different client.' });
      if (rec.revoked) return json(res, 400, { error: 'invalid_grant',
        error_description: 'Revoked, most likely because a replay was detected '
          + 'earlier in its family.' });

      // ===== reuse detection: the whole point of rotation (RFC 9700 4.14) ====
      if (rec.used) {
        if (policy.detect_reuse) {
          const killed = revokeFamily(rec.familyId);
          log('reject', 'REFRESH REUSE - revoked family of ' + killed);
          return json(res, 400, { error: 'invalid_grant',
            error_description: 'This refresh token was already used (generation '
              + rec.generation + '). Either it was stolen and replayed, or the real '
              + 'client lost the rotated value. The server cannot tell, so the whole '
              + 'family is revoked and the user must sign in again.' });
        }
        log('warn', 'reused refresh token ACCEPTED - detect_reuse is off');
      }
      if (rec.expiresAt < now()) return json(res, 400, { error: 'invalid_grant',
        error_description: 'Refresh token expired.' });

      // Scope may only ever shrink on refresh.
      let scope = rec.scope;
      if (body.get('scope')) {
        const asked = splitScope(body.get('scope'));
        const up = asked.filter((s) => !rec.scope.includes(s));
        if (up.length) return json(res, 400, { error: 'invalid_scope',
          error_description: 'Cannot widen scope on refresh: ' + up.join(' ') });
        scope = asked;
      }

      rec.used = true;
      const at = issueAccessToken(clientId, rec.sub, scope, rec.familyId);
      const out = { access_token: at.token, token_type: 'Bearer',
                    expires_in: TTL.access, scope: scope.join(' ') };
      if (policy.rotate_refresh) {
        out.refresh_token = issueRefreshToken(clientId, rec.sub, scope, rec.familyId,
                                              rec.generation + 1);
        log('ok', 'refresh ROTATED to generation ' + (rec.generation + 1));
      } else {
        rec.used = false;
        out.refresh_token = presented;
        log('warn', 'refresh NOT rotated - the same value keeps being reused');
      }
      return json(res, 200, out);
    }

    // ===== client_credentials: no user, so nobody to consent =====
    if (grant === 'client_credentials') {
      const asked = body.get('scope') ? splitScope(body.get('scope')) : client.scopes;
      const bad = asked.filter((s) => !client.scopes.includes(s));
      if (bad.length) return json(res, 400, { error: 'invalid_scope',
        error_description: 'Not registered for: ' + bad.join(' ') });
      // sub is the CLIENT. Never use this grant to act on a user's behalf.
      // `resource` (RFC 8707) lets the caller name which API the token is for;
      // Lab 8 uses it to mint a token this API must refuse.
      const at = issueAccessToken(clientId, clientId, asked, null,
                                  body.get('resource'));
      log('ok', 'client_credentials token issued (no user involved)');
      return json(res, 200, { access_token: at.token, token_type: 'Bearer',
        expires_in: TTL.access, scope: asked.join(' ') });
    }

    // ===== device grant: the agent polls here until the human approves =====
    if (grant === DEVICE_GRANT) {
      const entry = devices.get(body.get('device_code'));
      if (!entry || entry.clientId !== clientId)
        return json(res, 400, { error: 'invalid_grant' });
      if (entry.expiresAt < now()) {
        devices.delete(body.get('device_code'));
        return json(res, 400, { error: 'expired_token',
          error_description: 'The human did not approve in time.' });
      }
      // Enforce the polling interval we advertised, so one agent cannot
      // hammer the token endpoint while it waits.
      if (now() - entry.lastPoll < 5) {
        entry.lastPoll = now();
        return json(res, 400, { error: 'slow_down',
          error_description: 'Poll no faster than the advertised interval.' });
      }
      entry.lastPoll = now();

      if (entry.status === 'pending') return json(res, 400, {
        error: 'authorization_pending',
        error_description: 'Waiting for the human to approve.' });
      if (entry.status === 'denied') {
        devices.delete(body.get('device_code'));
        return json(res, 400, { error: 'access_denied' });
      }

      devices.delete(body.get('device_code'));   // single use, like a code
      const familyId = C.randomId(8);
      const at = issueAccessToken(clientId, entry.sub, entry.scope, familyId,
                                 API, { details: entry.details });
      log('ok', 'device grant completed for ' + entry.sub);
      const out = { access_token: at.token, token_type: 'Bearer',
        expires_in: TTL.access, scope: entry.scope.join(' ') };
      if (client.grants.includes('refresh_token'))
        out.refresh_token = issueRefreshToken(clientId, entry.sub, entry.scope,
                                              familyId, 1);
      if (entry.details) out.authorization_details = entry.details;
      return json(res, 200, out);
    }

    // ===== token exchange (RFC 8693): act on someone else's behalf =====
    // A service that received a user's token swaps it for a downstream one.
    // The new token names the caller in `act`, so the API sees the chain.
    if (grant === EXCHANGE_GRANT) {
      const subject = body.get('subject_token');
      const crypto2 = require('node:crypto');
      const r = C.verifyJwt(subject, {
        getKey: (kid) => kid === KEY.kid
          ? crypto2.createPublicKey({ key: KEY.publicJwk, format: 'jwk' }) : null,
        issuer: ISSUER, requiredTyp: 'at+jwt' });
      if (!r.valid) return json(res, 400, { error: 'invalid_grant',
        error_description: 'subject_token is not valid here (' + r.reason + ').' });

      // Delegation may only ever NARROW. You cannot exchange your way up.
      const held = splitScope(r.payload.scope);
      const asked = body.get('scope') ? splitScope(body.get('scope')) : held;
      const up = asked.filter((x) => !held.includes(x));
      if (up.length) return json(res, 400, { error: 'invalid_scope',
        error_description: 'Cannot widen scope on exchange: ' + up.join(' ') });

      const parsed = parseDetails(body.get('authorization_details'));
      if (parsed && parsed.error) return json(res, 400, {
        error: 'invalid_authorization_details', error_description: parsed.error });

      // Build the actor chain: whoever was acting before is nested inside.
      const act = { sub: clientId };
      if (r.payload.act) act.act = r.payload.act;
      const at = issueAccessToken(clientId, r.payload.sub, asked, null,
        body.get('resource') || API,
        { act, details: parsed ? parsed.value : r.payload.authorization_details });
      log('ok', 'token exchanged: ' + clientId + ' now acting for ' + r.payload.sub);
      return json(res, 200, { access_token: at.token, token_type: 'Bearer',
        issued_token_type: ACCESS_TOKEN_TYPE,
        expires_in: TTL.access, scope: asked.join(' ') });
    }

    return json(res, 400, { error: 'unsupported_grant_type' });
  }

  // ---------------- OIDC: /userinfo ----------------
  // Claims about the signed-in user, filtered by the scopes actually granted.
  // Note what it takes: the ACCESS token, not the ID token. The ID token is
  // for the client to read; this endpoint is for fetching fresh claims.
  if (path === '/userinfo') {
    const header = req.headers.authorization || '';
    if (!header.toLowerCase().startsWith('bearer '))
      return json(res, 401, { error: 'invalid_token',
        error_description: 'Send the access token as a Bearer token.' });

    const crypto3 = require('node:crypto');
    const r = C.verifyJwt(header.slice(7).trim(), {
      getKey: (kid) => kid === KEY.kid
        ? crypto3.createPublicKey({ key: KEY.publicJwk, format: 'jwk' }) : null,
      issuer: ISSUER, requiredTyp: 'at+jwt' });
    if (!r.valid) return json(res, 401, { error: 'invalid_token', reason: r.reason });

    const granted = splitScope(r.payload.scope);
    if (!granted.includes('openid'))
      return json(res, 403, { error: 'insufficient_scope',
        error_description: 'The openid scope is what turns an OAuth grant into an '
          + 'OpenID Connect one. Without it there is no identity to return.' });

    const user = userBySub(r.payload.sub);
    if (!user) return json(res, 404, { error: 'no_such_subject',
      error_description: 'A machine token has no user behind it.' });

    // `sub` is always present; everything else is gated on scope, exactly as
    // the claims in the ID token are.
    const claims = { sub: user.sub };
    if (granted.includes('profile')) {
      claims.name = user.name;
      claims.preferred_username = usernameOf(user.sub);
    }
    if (granted.includes('email')) {
      claims.email = user.email;
      claims.email_verified = true;
    }
    log('ok', '/userinfo for ' + user.sub + ' -> ' + Object.keys(claims).join(', '));
    return json(res, 200, claims);
  }

  // ---------------- introspection (RFC 7662) ----------------
  // Must be protected: it answers questions about other people's tokens.
  if (path === '/introspect' && req.method === 'POST') {
    const auth = authenticateClient(req, body);
    if (auth.error || auth.client.type !== 'confidential')
      return json(res, 401, { error: 'invalid_client' });
    const crypto = require('node:crypto');
    const r = C.verifyJwt(body.get('token'), {
      getKey: (kid) => kid === KEY.kid
        ? crypto.createPublicKey({ key: KEY.publicJwk, format: 'jwk' }) : null,
      issuer: ISSUER });
    if (!r.valid) return json(res, 200, { active: false, reason: r.reason });
    if (revoked.has(r.payload.jti)) return json(res, 200, { active: false,
      reason: 'revoked',
      note: 'The signature is fine but this jti was revoked before exp. Only '
        + 'introspection can see that - local JWT validation cannot.' });
    return json(res, 200, { active: true, scope: r.payload.scope, sub: r.payload.sub,
      client_id: r.payload.client_id, aud: r.payload.aud, exp: r.payload.exp,
      jti: r.payload.jti, token_type: 'Bearer' });
  }

  // ---------------- revocation (RFC 7009) ----------------
  // Always answers 200, even for an unknown token: this endpoint must not
  // become an oracle for guessing valid ones.
  if (path === '/revoke' && req.method === 'POST') {
    const auth = authenticateClient(req, body);
    if (auth.error) return json(res, 401, { error: auth.error });
    const token = body.get('token');
    const rec = refresh.get(C.sha256hex(String(token || '')));
    if (rec) {
      const n = revokeFamily(rec.familyId);
      log('ok', 'revoked refresh family (' + n + ' tokens)');
      return json(res, 200, { revoked: true, family_tokens: n });
    }
    const d = C.decodeJwt(token);
    if (d && d.payload.jti) {
      revoked.add(d.payload.jti);
      log('ok', 'revoked access token jti=' + d.payload.jti);
      return json(res, 200, { revoked: true,
        note: 'An API doing local JWT validation will keep accepting this until exp. '
          + 'That is the trade-off of self-contained tokens.' });
    }
    return json(res, 200,
      { revoked: true, note: 'Unknown token; 200 anyway per RFC 7009.' });
  }

  return json(res, 404, { error: 'not_found', path });
});

// --- the IdP's two screens ------------------------------------------------
function resume(res, rid, sid) {
  const r = requests.get(rid);
  if (!r) return html(res, '<h2>Expired</h2>', 400);
  const s = sessions.get(sid);
  if (!s || s.expiresAt < now()) return loginPage(res, rid, r);
  return consentPage(res, rid, r, userBySub(s.sub));
}

function loginPage(res, rid, r, error) {
  return html(res,
    '<h2>Sign in</h2><p><b>' + esc(r.clientId) + '</b> wants to act on your behalf. '
    + 'Notice you are typing your password into the <b>identity provider</b> '
    + '(localhost:7000), never into the application. That is the whole point.</p>'
    + (error ? '<p style="color:#c0203f"><b>' + esc(error) + '</b></p>' : '')
    + '<form method=POST action=/login>'
    + '<input type=hidden name=request_id value="' + esc(rid) + '">'
    + '<p><label>Username<br><input name=username autofocus></label>'
    + '<p><label>Password<br><input name=password type=password></label>'
    + '<p><button>Sign in</button></form>'
    + '<hr><p style="color:#666;font-size:13px">alice / wonderland &middot; '
    + 'bob / builder (bob cannot grant payments:write)</p>');
}

function consentPage(res, rid, r, user) {
  const grantable = r.scope.filter((s) => user.can.includes(s));
  const refused = r.scope.filter((s) => !user.can.includes(s));
  return html(res,
    '<h2>Allow access?</h2><p>Signed in as <b>' + esc(user.name) + '</b>. '
    + '<b>' + esc(r.clientId) + '</b> is asking for:</p>'
    + (refused.length ? '<p style="color:#a8620a">' + esc(user.name) + ' cannot '
        + 'delegate <code>' + esc(refused.join(' ')) + '</code>, so it is dropped. A '
        + 'client never receives more authority than the user has.</p>' : '')
    + '<form method=POST action=/consent>'
    + '<input type=hidden name=request_id value="' + esc(rid) + '">'
    + grantable.map((s) => '<p><label><input type=checkbox name=scope value="'
        + esc(s) + '" checked' + (s === 'openid' ? ' disabled' : '') + '> <code>'
        + esc(s) + '</code></label>').join('')
    + '<p><button name=action value=allow>Allow</button> '
    + '<button name=action value=deny>Deny</button></form>');
}

server.listen(PORT, () => {
  console.log('\n  Authorization server + IdP  ' + ISSUER);
  console.log('  discovery  ' + ISSUER + '/.well-known/oauth-authorization-server');
  console.log('  policy     ' + ISSUER + '/policy      (turn rules off for Lab 3)');
  console.log('  signing kid ' + KEY.kid + '\n');
});
