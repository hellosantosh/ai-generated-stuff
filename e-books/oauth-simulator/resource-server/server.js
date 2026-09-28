'use strict';
/**
 * Resource Server -- the API that owns the data.
 *
 * The resource server is the whole reason OAuth exists, and it is the part
 * most tutorials skip. Its job on every request is exactly four questions:
 *
 *   1. Is there a Bearer token?                      -> 401 invalid_token
 *   2. Is the signature real, and mine to trust?     -> 401 invalid_token
 *   3. Was it minted for THIS api, and still valid?  -> 401 invalid_token
 *   4. Does its scope cover THIS operation?          -> 403 insufficient_scope
 *
 * Note what is *not* on that list: the API never sees a password, never talks
 * to the user, and never asks the authorization server anything at all in the
 * common case -- step 2 is offline, using published public keys.
 */
const { App, bearerToken, fetchJson } = require('../shared/http');
const { createTracer } = require('../shared/trace');
const { page, card, pre, preJson, note, pill, kvTable, escapeHtml } = require('../shared/ui');
const jose = require('../shared/jose');

const PORT = Number(process.env.PORT || 9010);
/** The public identity of this API. Access tokens must name it in `aud`. */
const AUDIENCE = process.env.AUDIENCE || 'http://localhost:9010';
/** The issuer we trust. Anything signed by anyone else is rejected outright. */
const TRUSTED_ISSUER = process.env.TRUSTED_ISSUER || 'http://localhost:9000';
/** Where to reach the AS from inside the network (service name in Docker). */
const AS_INTERNAL = process.env.AS_INTERNAL_URL || TRUSTED_ISSUER;
const CLIENT_ID = process.env.RS_CLIENT_ID || 'demo-service';
const CLIENT_SECRET = process.env.RS_CLIENT_SECRET || 'service-super-secret';

const trace = createTracer('resource');
const app = new App({ name: 'resource-server' });

/* ------------------------------------------------------------------ *
 * The API's own data. The token says WHO is asking and WHAT they may
 * do; the data itself belongs to the API, not to the token.
 * ------------------------------------------------------------------ */
const DB = {
  'u-1001': {
    profile: { display_name: 'Alice Liddell', member_since: '2019-04-02', tier: 'gold' },
    accounts: [
      { id: 'ACC-4410', label: 'Everyday Checking', balance: 4820.55, currency: 'USD' },
      { id: 'ACC-9982', label: 'Rainy Day Savings', balance: 15200.0, currency: 'USD' },
    ],
    payments: [],
  },
  'u-1002': {
    profile: { display_name: 'Bob Builder', member_since: '2021-11-19', tier: 'standard' },
    accounts: [{ id: 'ACC-7100', label: 'Joint Account', balance: 310.2, currency: 'USD' }],
    payments: [],
  },
};

/* ------------------------------------------------------------------ *
 * JWKS cache. Fetch the AS's public keys once, cache them, and refetch
 * when an unknown `kid` shows up -- that is how key rotation works
 * without downtime or shared secrets.
 * ------------------------------------------------------------------ */
const jwks = { keys: new Map(), fetchedAt: 0, lastError: null };

async function refreshJwks(reason) {
  const url = `${AS_INTERNAL}/jwks.json`;
  try {
    const { json, status } = await fetchJson(url);
    if (!json || !Array.isArray(json.keys)) throw new Error(`unexpected JWKS body (HTTP ${status})`);
    jwks.keys.clear();
    for (const jwk of json.keys) jwks.keys.set(jwk.kid, jose.jwkToPublicKey(jwk));
    jwks.fetchedAt = Date.now();
    jwks.lastError = null;
    trace.ok(`JWKS fetched (${reason})`, { url, kids: [...jwks.keys.keys()] });
    return true;
  } catch (err) {
    jwks.lastError = err.message;
    trace.error('could not fetch JWKS', { url, error: err.message });
    return false;
  }
}

async function getKeyForKid(kid) {
  if (!jwks.keys.size) await refreshJwks('cold start');
  if (kid && !jwks.keys.has(kid)) await refreshJwks(`unknown kid "${kid}" -- possible key rotation`);
  return jwks.keys.get(kid) || null;
}

/* ------------------------------------------------------------------ *
 * The guard every protected route runs through.
 * ------------------------------------------------------------------ */
async function authorize(ctx, requiredScopes) {
  const token = bearerToken(ctx.req);

  if (!token) {
    // RFC 6750: say what is needed, never say anything about the data.
    ctx.res.header('www-authenticate', `Bearer realm="${AUDIENCE}", scope="${requiredScopes.join(' ')}"`);
    trace.error(`401 on ${ctx.method} ${ctx.url.pathname}: no Bearer token`, {});
    return { ok: false, status: 401, body: { error: 'invalid_token', error_description: 'This endpoint requires an OAuth 2.1 Bearer access token in the Authorization header.', required_scope: requiredScopes } };
  }

  const decoded = jose.decodeJwt(token);
  const kid = decoded && decoded.header ? decoded.header.kid : null;
  let key = await getKeyForKid(kid);

  const check = () => jose.verifyJwt(token, {
    getKey: () => key,
    issuer: TRUSTED_ISSUER,
    audience: AUDIENCE,
    requiredTyp: 'at+jwt',
  });
  let result = check();

  // A signature failure on a kid we thought we knew usually means the key
  // behind that id changed. Refetch once (rate-limited) and try again before
  // blaming the caller -- otherwise every client breaks until this restarts.
  if (!result.valid && result.reason === 'bad_signature' && Date.now() - jwks.fetchedAt > 5000) {
    trace.warn('signature failed for a cached key -- refetching JWKS once before rejecting', { kid });
    if (await refreshJwks('signature failure on a known kid')) {
      key = jwks.keys.get(kid) || null;
      result = check();
      if (result.valid) trace.ok('retry succeeded: the authorization server had rotated its key', { kid });
    }
  }

  if (!result.valid) {
    ctx.res.header('www-authenticate', `Bearer realm="${AUDIENCE}", error="invalid_token", error_description="${result.reason}"`);
    trace.error(`401 on ${ctx.method} ${ctx.url.pathname}: ${result.reason}`, { detail: result.detail });
    return {
      ok: false, status: 401,
      body: {
        error: 'invalid_token', error_description: result.detail,
        'x-simulator-check-failed': result.reason,
        'x-simulator-hint': {
          malformed_token: 'The value is not a JWT. Did you send a refresh token or an opaque string?',
          unsupported_alg: 'Only RS256 is accepted. Never trust the alg header from the token itself.',
          unexpected_typ: 'Expected typ="at+jwt". You most likely sent the ID TOKEN instead of the access token -- a very common bug.',
          unknown_kid: 'No published key matches. The AS may have rotated keys (it regenerates them on restart).',
          bad_signature: 'The token was modified, or was signed by something other than the trusted issuer.',
          issuer_mismatch: `This API only trusts tokens from ${TRUSTED_ISSUER}.`,
          audience_mismatch: `This API only accepts tokens whose aud is "${AUDIENCE}". A token for another API must not work here.`,
          token_expired: 'Access tokens are deliberately short-lived. Use the refresh token to get a new one.',
        }[result.reason] || null,
      },
    };
  }

  const granted = String(result.payload.scope || '').split(/\s+/).filter(Boolean);
  const missing = requiredScopes.filter((s) => !granted.includes(s));
  if (missing.length) {
    // RFC 6750 §3.1: the token is fine, the *permission* is not. 403, not 401.
    ctx.res.header('www-authenticate', `Bearer realm="${AUDIENCE}", error="insufficient_scope", scope="${requiredScopes.join(' ')}"`);
    trace.error(`403 on ${ctx.method} ${ctx.url.pathname}: insufficient_scope`, { granted, required: requiredScopes, missing });
    return {
      ok: false, status: 403,
      body: {
        error: 'insufficient_scope',
        error_description: `This operation needs scope "${missing.join(' ')}" but the token only carries "${granted.join(' ') || '(none)'}". The token is perfectly valid -- it simply does not authorize this action. That is why the status is 403 and not 401: refreshing will not help, the user has to grant more.`,
        required_scope: requiredScopes, granted_scope: granted,
      },
    };
  }

  trace.ok(`${ctx.method} ${ctx.url.pathname} authorized`, {
    sub: result.payload.sub, client_id: result.payload.client_id, scope: result.payload.scope, jti: result.payload.jti,
  });
  return { ok: true, claims: result.payload, token };
}

/** Wrap a protected route: scope check first, handler second. */
function protect(requiredScopes, handler) {
  return async (ctx) => {
    ctx.res.header('access-control-allow-origin', ctx.req.headers.origin || '*')
      .header('access-control-expose-headers', 'www-authenticate')
      .header('vary', 'origin');
    const auth = await authorize(ctx, requiredScopes);
    if (!auth.ok) return ctx.res.json(auth.body, auth.status);
    return handler(ctx, auth);
  };
}

const preflight = (ctx) => ctx.res
  .header('access-control-allow-origin', ctx.req.headers.origin || '*')
  .header('access-control-allow-headers', 'authorization,content-type')
  .header('access-control-allow-methods', 'GET,POST,OPTIONS')
  .text('', 204);

/* ------------------------------------------------------------------ *
 * Protected endpoints
 * ------------------------------------------------------------------ */
app.options('/api/*', preflight);

app.get('/api/me', protect(['profile'], (ctx, { claims }) => {
  const record = DB[claims.sub];
  if (!record) {
    return ctx.res.json({
      error: 'no_such_subject',
      error_description: `The token is valid but sub="${claims.sub}" is not a user in this API. This is what a client_credentials token looks like here: a valid app identity with no human behind it.`,
      sub: claims.sub,
    }, 404);
  }
  return ctx.res.json({ sub: claims.sub, ...record.profile, authorized_via: { client_id: claims.client_id, scope: claims.scope } });
}));

app.get('/api/accounts', protect(['accounts:read'], (ctx, { claims }) => {
  const record = DB[claims.sub];
  const accounts = record ? record.accounts : [];
  return ctx.res.json({
    sub: claims.sub,
    accounts,
    'x-simulator-note': `Returned ${accounts.length} account(s) for sub="${claims.sub}". The API chose the data from the token's sub -- never from a user id in the request. Trusting a client-supplied user id here is how you build an IDOR vulnerability on top of perfectly good OAuth.`,
  });
}));

app.post('/api/payments', protect(['payments:write'], (ctx, { claims }) => {
  const { to, amount, from } = ctx.body.fields || {};
  const record = DB[claims.sub];
  if (!record) return ctx.res.json({ error: 'no_such_subject', sub: claims.sub }, 404);
  const source = record.accounts.find((a) => a.id === from) || record.accounts[0];
  const payment = {
    id: `PAY-${Math.random().toString(36).slice(2, 8).toUpperCase()}`,
    from: source ? source.id : null,
    to: to || 'ACC-EXTERNAL',
    amount: Number(amount || 25),
    currency: 'USD',
    status: 'accepted',
    created_at: new Date().toISOString(),
  };
  record.payments.push(payment);
  trace.warn('payment created -- this is why payments:write needs explicit consent', { sub: claims.sub, amount: payment.amount });
  return ctx.res.json({ payment, authorized_via: { client_id: claims.client_id, scope: claims.scope } }, 201);
}));

/**
 * The same data, authorized by *introspection* instead of local JWT checks.
 * Slower (a network call per request) but it sees revocation instantly.
 */
app.get('/api/accounts-introspected', async (ctx) => {
  const token = bearerToken(ctx.req);
  if (!token) return ctx.res.json({ error: 'invalid_token', error_description: 'Bearer token required.' }, 401);

  trace.info('asking the AS to introspect this token (RFC 7662) instead of validating locally', {});
  const body = new URLSearchParams({ token, token_type_hint: 'access_token' });
  const { json, status } = await fetchJson(`${AS_INTERNAL}/introspect`, {
    method: 'POST',
    headers: {
      'content-type': 'application/x-www-form-urlencoded',
      authorization: `Basic ${Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString('base64')}`,
    },
    body: body.toString(),
  });

  if (!json || json.active !== true) {
    trace.error('introspection says the token is NOT active', { status, reason: json && json['x-simulator-reason'] });
    return ctx.res.json({
      error: 'invalid_token',
      error_description: 'The authorization server says this token is not active.',
      introspection_response: json,
      'x-simulator-note': 'Local JWT validation would still have accepted a revoked-but-unexpired token. Introspection is the only way an API learns about revocation immediately -- at the cost of a round trip per request.',
    }, 401);
  }
  const granted = String(json.scope || '').split(/\s+/).filter(Boolean);
  if (!granted.includes('accounts:read')) return ctx.res.json({ error: 'insufficient_scope', required_scope: ['accounts:read'], granted_scope: granted }, 403);
  const record = DB[json.sub];
  trace.ok('introspection says the token is active', { sub: json.sub, scope: json.scope });
  return ctx.res.json({
    sub: json.sub, accounts: record ? record.accounts : [],
    validated_by: 'token introspection (RFC 7662) -- one network call to the AS per request',
    introspection_response: json,
  });
});

/* ------------------------------------------------------------------ *
 * Diagnostics
 * ------------------------------------------------------------------ */
app.get('/healthz', (ctx) => ctx.res.json({
  status: 'ok', service: 'resource-server', audience: AUDIENCE,
  trusted_issuer: TRUSTED_ISSUER, jwks_kids: [...jwks.keys.keys()], jwks_error: jwks.lastError,
}));

/** Explain, claim by claim, what this API thinks of a token you paste in. */
app.post('/debug/token', async (ctx) => {
  const token = (ctx.body.fields || {}).token || bearerToken(ctx.req);
  if (!token) return ctx.res.json({ error: 'send a token as form field "token" or an Authorization header' }, 400);
  const decoded = jose.decodeJwt(token);
  const key = await getKeyForKid(decoded && decoded.header ? decoded.header.kid : null);
  const result = jose.verifyJwt(token, { getKey: () => key, issuer: TRUSTED_ISSUER, audience: AUDIENCE, requiredTyp: 'at+jwt' });
  const now = Math.floor(Date.now() / 1000);
  return ctx.res.json({
    header: decoded && decoded.header,
    payload: decoded && decoded.payload,
    checks: {
      signature: result.reason === 'bad_signature' ? 'FAIL' : (decoded ? 'PASS' : 'n/a'),
      issuer: decoded && decoded.payload.iss === TRUSTED_ISSUER ? 'PASS' : `FAIL (expected ${TRUSTED_ISSUER})`,
      audience: decoded && [].concat(decoded.payload.aud || []).includes(AUDIENCE) ? 'PASS' : `FAIL (expected ${AUDIENCE})`,
      typ: decoded && decoded.header.typ === 'at+jwt' ? 'PASS' : `FAIL (expected at+jwt, got ${decoded && decoded.header.typ})`,
      expiry: decoded && decoded.payload.exp > now ? `PASS (${decoded.payload.exp - now}s left)` : 'FAIL (expired)',
    },
    verdict: result.valid ? 'This API would accept this token.' : `This API would reject this token: ${result.reason}`,
    detail: result.detail || null,
  });
});

app.get('/trace.json', (ctx) => ctx.res.json({ events: trace.all(Number(ctx.query.since || 0)) }));
app.get('/trace/stream', (ctx) => {
  const stream = ctx.res.sse();
  for (const e of trace.all()) stream.send('trace', e);
  stream.onClose(trace.subscribe((e) => stream.send('trace', e)));
});

app.get('/', (ctx) => {
  const rows = [
    ['GET', '/api/me', 'profile', 'Profile of whoever the token names in `sub`.'],
    ['GET', '/api/accounts', 'accounts:read', 'Account list and balances.'],
    ['POST', '/api/payments', 'payments:write', 'Move money. The dangerous one.'],
    ['GET', '/api/accounts-introspected', 'accounts:read', 'Same data, authorized by RFC 7662 introspection instead of local validation.'],
    ['POST', '/debug/token', '-', 'Paste any token and see every check this API runs.'],
  ].map(([m, p, s, d]) => `<tr><td>${pill(m.toLowerCase(), m === 'GET' ? 'get' : 'post')}</td><td class="v">${p}</td><td class="v">${s}</td><td class="tiny">${d}</td></tr>`).join('');

  const body = `
${card(`<h2>How this API authorizes a request</h2>
<ol class="small" style="padding-left:20px;line-height:1.9">
  <li>Pull the Bearer token out of the <span class="mono">Authorization</span> header. No token &rarr; <b>401</b>.</li>
  <li>Read the <span class="mono">kid</span> from the JWT header, look it up in the cached JWKS, verify the RS256 signature. Bad signature &rarr; <b>401</b>.</li>
  <li>Check <span class="mono">iss</span>, <span class="mono">aud</span>, <span class="mono">exp</span>, <span class="mono">typ</span>. Wrong issuer or wrong audience &rarr; <b>401</b>.</li>
  <li>Check the <span class="mono">scope</span> claim covers this operation. Valid token, missing scope &rarr; <b>403 insufficient_scope</b>.</li>
  <li>Serve data belonging to <span class="mono">sub</span> &mdash; from the token, never from a user id in the request.</li>
</ol>
${note('Steps 1-4 need no contact with the authorization server at all. That is the point of signed, self-contained tokens: the API verifies them offline using published public keys.')}`)}
${card(`<h2>Endpoints</h2><table><thead><tr><th>Method</th><th>Path</th><th>Required scope</th><th></th></tr></thead><tbody>${rows}</tbody></table>`)}
${card(`<h2>Configuration</h2>${kvTable({ audience: AUDIENCE, trusted_issuer: TRUSTED_ISSUER, jwks_uri: `${AS_INTERNAL}/jwks.json`, cached_kids: [...jwks.keys.keys()].join(', ') || '(not fetched yet)' })}
<p class="tiny muted">A token whose <span class="mono">aud</span> is not <span class="mono">${escapeHtml(AUDIENCE)}</span> is rejected even if the signature is perfect. That is how you stop one API from replaying a token at another.</p>`)}
${card(`<h3>Try it from a terminal</h3>${pre(`# 401: no token at all
curl -i ${AUDIENCE}/api/accounts

# 200: with a token from the client app or ./scripts/demo-flow.sh
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" ${AUDIENCE}/api/accounts | jq

# 403: valid token, wrong scope
curl -i -X POST -H "Authorization: Bearer $ACCESS_TOKEN" ${AUDIENCE}/api/payments`)}`)}`;
  return page({
    title: 'Resource Server',
    subtitle: 'The API holding the data. It trusts tokens from one issuer, for one audience, and enforces scope on every route.',
    body,
    nav: [{ label: 'Client app', href: 'http://localhost:9020' }, { label: 'Authorization server', href: 'http://localhost:9000' }, { label: 'Health', href: '/healthz' }],
    brand: 'Simulator Resource Server',
    footer: 'Teaching simulator. All data is in memory and resets on restart.',
  });
});

refreshJwks('startup').finally(() => {
  app.listen(PORT, (port) => {
    trace.ok(`resource server listening on :${port}`, { audience: AUDIENCE, trusted_issuer: TRUSTED_ISSUER });
    console.log(`\n  Resource Server  ${AUDIENCE}`);
    console.log(`  Trusts issuer    ${TRUSTED_ISSUER}`);
    console.log(`  JWKS             ${AS_INTERNAL}/jwks.json\n`);
  });
});
