'use strict';
// ===========================================================================
// LAB FILE 3 of 4 - api-server.js          run:  node api-server.js
// The resource server: the API that owns the data. On every request it runs
// the four checks from Chapter 12, and nothing else.
// ===========================================================================
const http = require('node:http');
const crypto = require('node:crypto');
const C = require('./crypto-lab');

const PORT = 7010;
const AUDIENCE = 'http://localhost:7010';        // tokens must name US in `aud`
const TRUSTED_ISSUER = 'http://localhost:7000';  // and be signed by THEM

// The API's own data. The token says WHO is asking and WHAT they may do; the
// data belongs to the API, not to the token.
const DB = {
  'u-1001': { profile: { name: 'Alice Liddell', tier: 'gold' }, payments: [],
    accounts: [{ id: 'ACC-4410', label: 'Everyday Checking', balance: 4820.55 },
               { id: 'ACC-9982', label: 'Rainy Day Savings', balance: 15200.0 }] },
  'u-1002': { profile: { name: 'Bob Builder', tier: 'standard' }, payments: [],
    accounts: [{ id: 'ACC-7100', label: 'Joint Account', balance: 310.2 }] },
};

// --- the JWKS cache -------------------------------------------------------
// Fetch the issuer's public keys once, cache them by kid, and refetch when an
// unknown kid appears. That is how key rotation works with no shared secret
// and no downtime. Restarting the AS rotates its key - watch this recover.
const jwks = { keys: new Map(), fetchedAt: 0 };

async function refreshJwks(why) {
  const r = await fetch(TRUSTED_ISSUER + '/jwks.json');
  const body = await r.json();
  jwks.keys.clear();
  for (const jwk of body.keys)
    jwks.keys.set(jwk.kid, crypto.createPublicKey({ key: jwk, format: 'jwk' }));
  jwks.fetchedAt = Date.now();
  log('ok', 'JWKS fetched (' + why + '): ' + [...jwks.keys.keys()].join(', '));
}

async function keyFor(kid) {
  if (!jwks.keys.size) await refreshJwks('cold start');
  if (kid && !jwks.keys.has(kid)) await refreshJwks('unknown kid ' + kid);
  return jwks.keys.get(kid) || null;
}

const log = (tag, msg) => console.log('  [api] ' + tag.padEnd(7) + msg);

function readBody(req) {
  return new Promise((resolve) => {
    let raw = '';
    req.on('data', (c) => { raw += c; if (raw.length > 1e5) req.destroy(); });
    req.on('end', () => resolve(new URLSearchParams(raw)));
  });
}

// Flatten the RFC 8693 `act` chain into something loggable:
//   alice  <-  demo-orchestrator  <-  demo-agent
function actorChain(claims) {
  const chain = [];
  for (let a = claims.act; a; a = a.act) chain.push(a.sub);
  return chain;
}

// Find the structured grant (RFC 9396) that covers a given action.
function detailFor(claims, type) {
  return (claims.authorization_details || []).find((d) => d.type === type) || null;
}
const json = (res, status, obj) => (res.writeHead(status,
  { 'content-type': 'application/json' }), res.end(JSON.stringify(obj, null, 2)));

// ===========================================================================
// The four checks, in order. This function IS the resource server.
// ===========================================================================
async function authorize(req, res, required) {
  // 1. Is there a Bearer token?                          -> 401
  const header = req.headers.authorization || '';
  if (!header.toLowerCase().startsWith('bearer ')) {
    res.setHeader('www-authenticate',
      'Bearer realm="' + AUDIENCE + '", scope="' + required.join(' ') + '"');
    log('401', 'no Bearer token');
    return { body: { error: 'invalid_token',
      error_description: 'This endpoint needs an OAuth 2.1 Bearer access token.' },
      status: 401 };
  }
  const token = header.slice(7).trim();

  // 2. Is the signature real and mine to trust?          -> 401
  // 3. Was it minted for THIS api, and is it still live? -> 401
  const decoded = C.decodeJwt(token);
  const kid = decoded && decoded.header ? decoded.header.kid : null;
  const key = await keyFor(kid);
  let result = C.verifyJwt(token, { getKey: () => key, issuer: TRUSTED_ISSUER,
    audience: AUDIENCE, requiredTyp: 'at+jwt' });

  // A signature failure on a kid we thought we knew usually means the key
  // behind it changed. Refetch once before blaming the caller.
  if (!result.valid && result.reason === 'bad_signature'
      && Date.now() - jwks.fetchedAt > 5000) {
    await refreshJwks('signature failure on a known kid');
    result = C.verifyJwt(token, { getKey: () => jwks.keys.get(kid) || null,
      issuer: TRUSTED_ISSUER, audience: AUDIENCE, requiredTyp: 'at+jwt' });
  }

  if (!result.valid) {
    res.setHeader('www-authenticate', 'Bearer error="invalid_token"');
    log('401', result.reason);
    return { status: 401, body: { error: 'invalid_token', check_failed: result.reason,
      error_description: {
        unexpected_typ: 'Expected typ="at+jwt". You almost certainly sent the ID '
          + 'TOKEN instead of the access token - a very common bug.',
        audience_mismatch: 'This token was minted for another audience. A perfect '
          + 'signature does not make a token valid HERE.',
        unsupported_alg: 'Only RS256 is accepted. Never trust the token\'s own alg.',
        unknown_kid: 'No published key matches. The AS may have rotated keys.',
        bad_signature: 'Altered, or signed by something other than the issuer.',
        token_expired: 'Access tokens are short-lived on purpose. Refresh it.',
        issuer_mismatch: 'This API only trusts ' + TRUSTED_ISSUER + '.',
      }[result.reason] || result.reason } };
  }

  // 4. Does the scope cover THIS operation?              -> 403, not 401
  const granted = String(result.payload.scope || '').split(/\s+/).filter(Boolean);
  const missing = required.filter((s) => !granted.includes(s));
  if (missing.length) {
    res.setHeader('www-authenticate', 'Bearer error="insufficient_scope", scope="'
      + required.join(' ') + '"');
    log('403', 'insufficient_scope, needs ' + missing.join(' '));
    return { status: 403, body: { error: 'insufficient_scope',
      required_scope: required, granted_scope: granted,
      error_description: 'The token is perfectly valid - it simply does not '
        + 'authorize this action. That is why this is 403 and not 401: refreshing '
        + 'will not help, the user has to grant more.' } };
  }

  log('ok', req.method + ' ' + req.url + '  sub=' + result.payload.sub
    + ' scope="' + result.payload.scope + '"');
  return { claims: result.payload };
}

// ===========================================================================
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, AUDIENCE);
  const path = url.pathname;

  if (path === '/healthz') return json(res, 200,
    { ok: true, audience: AUDIENCE, cached_kids: [...jwks.keys.keys()] });

  // ---- profile, needs `profile` ----
  if (path === '/api/me') {
    const a = await authorize(req, res, ['profile']);
    if (a.status) return json(res, a.status, a.body);
    const rec = DB[a.claims.sub];
    // A client_credentials token has a client_id as its sub, and no human
    // behind it - so there is no profile to return.
    if (!rec) return json(res, 404, { error: 'no_such_subject', sub: a.claims.sub,
      note: 'Valid token, but sub is not a user. This is what a machine token '
        + 'looks like here.' });
    return json(res, 200, { sub: a.claims.sub, ...rec.profile });
  }

  // ---- accounts, needs `accounts:read` ----
  if (path === '/api/accounts') {
    const a = await authorize(req, res, ['accounts:read']);
    if (a.status) return json(res, a.status, a.body);
    const rec = DB[a.claims.sub];
    return json(res, 200, { sub: a.claims.sub, accounts: rec ? rec.accounts : [],
      acting_chain: actorChain(a.claims),
      note: 'The API picked the data from the token\'s `sub`, never from a user id '
        + 'in the request. Trusting a caller-supplied id here is how you build an '
        + 'IDOR on top of perfectly good OAuth.' });
  }

  // ---- payments, needs `payments:write` ----
  // Scope says the caller may move money at all. authorization_details says
  // how much, and out of which account. See Chapter 15.
  if (path === '/api/payments' && req.method === 'POST') {
    const body = await readBody(req);
    const a = await authorize(req, res, ['payments:write']);
    if (a.status) return json(res, a.status, a.body);
    const rec = DB[a.claims.sub];
    if (!rec) return json(res, 404, { error: 'no_such_subject' });

    const amount = Number(body.get('amount') || 42);
    const from = body.get('from') || rec.accounts[0].id;
    const chain = actorChain(a.claims);
    if (chain.length)
      log('info', 'acting chain: ' + a.claims.sub + ' <- ' + chain.join(' <- '));

    // If the token carries a structured grant, it BINDS the request. A scope
    // alone would have allowed any amount from any account.
    const grant = detailFor(a.claims, 'payment_initiation');
    if (grant) {
      const cap = Number(grant.maxAmount);
      if (Number.isFinite(cap) && amount > cap) {
        log('403', 'over the granted limit: ' + amount + ' > ' + cap);
        return json(res, 403, { error: 'insufficient_authorization',
          error_description: 'This token authorizes at most ' + grant.maxAmount
            + ' per payment; the request asked for ' + amount + '. The scope '
            + 'payments:write was satisfied - the structured limit was not.',
          granted: grant, requested: { amount, from } });
      }
      if (grant.fromAccount && grant.fromAccount !== from) {
        log('403', 'wrong source account: ' + from);
        return json(res, 403, { error: 'insufficient_authorization',
          error_description: 'This token only authorizes payments from '
            + grant.fromAccount + '.', granted: grant, requested: { amount, from } });
      }
    }

    const payment = { id: 'PAY-' + Math.random().toString(36).slice(2, 8).toUpperCase(),
      from, to: body.get('to') || 'ACC-EXTERNAL', amount, status: 'accepted',
      authorized_by: a.claims.sub, acting: chain.length ? chain : undefined,
      constrained_by: grant || undefined };
    rec.payments.push(payment);
    log('ok', 'payment ' + payment.id + ' for ' + amount);
    return json(res, 201, { payment });
  }

  // ---- the same data, authorized by introspection instead (Lab 8) ----
  // Slower - a network call per request - but it sees revocation instantly.
  if (path === '/api/accounts-introspected') {
    const header = req.headers.authorization || '';
    const token = header.toLowerCase().startsWith('bearer ') ? header.slice(7) : '';
    const r = await fetch(TRUSTED_ISSUER + '/introspect', { method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded',
        authorization: 'Basic '
          + Buffer.from('demo-service:service-secret').toString('base64') },
      body: new URLSearchParams({ token }).toString() });
    const intro = await r.json();
    if (!intro.active) {
      log('401', 'introspection says not active: ' + intro.reason);
      return json(res, 401, { error: 'invalid_token', introspection: intro,
        note: 'Local JWT validation would still have accepted a revoked-but-'
          + 'unexpired token. Introspection is the only way an API learns about '
          + 'revocation immediately - at the cost of a round trip per call.' });
    }
    const rec = DB[intro.sub];
    log('ok', 'introspection says active, sub=' + intro.sub);
    return json(res, 200, { sub: intro.sub, accounts: rec ? rec.accounts : [],
      validated_by: 'token introspection (RFC 7662)' });
  }

  return json(res, 404, { error: 'not_found', path });
});

server.listen(PORT, () => {
  console.log('\n  Resource server  ' + AUDIENCE);
  console.log('  trusts issuer    ' + TRUSTED_ISSUER);
  console.log('  scopes           /api/me=profile  /api/accounts=accounts:read'
    + '  /api/payments=payments:write\n');
});
