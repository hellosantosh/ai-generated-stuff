'use strict';
/**
 * OAuth 2.1 Authorization Server + Identity Provider.
 *
 * Implements, on Node core modules only:
 *   RFC 6749 (core, as narrowed by OAuth 2.1)  RFC 7636 (PKCE, mandatory)
 *   RFC 8414 (metadata discovery)              RFC 7662 (introspection)
 *   RFC 7009 (revocation)                      RFC 9700 (security BCP)
 *   RFC 8707 (resource indicators)             OpenID Connect Core (subset)
 *
 * Read this file top to bottom and you have read the protocol.
 */
const { App, parseBasicAuth, bearerToken } = require('../shared/http');
const { createTracer } = require('../shared/trace');
const jose = require('../shared/jose');
const cfg = require('./config');
const { Store, now } = require('./store');
const views = require('./views');

const trace = createTracer('authz');
const store = new Store();
const app = new App({ name: 'authorization-server', log: (lvl, msg, data) => trace.log(lvl === 'error' ? 'error' : 'info', msg, data) });

/** Signing keys are generated fresh at every boot: restart == rotate. */
const signingKey = jose.generateSigningKey(process.env.KEY_ID_PREFIX || 'sim');
const publicJwk = jose.publicKeyToJwk(signingKey.publicKeyPem, signingKey.kid);

/** Mutable copy of the policy so the /policy page can break things at runtime. */
const policy = { ...cfg.DEFAULT_POLICY };

const SESSION_COOKIE = 'as_idp_session';
const ALL_SCOPES = Object.keys(cfg.SCOPES);
const splitScope = (s) => String(s || '').trim().split(/\s+/).filter(Boolean);

/* ================================================================== *
 * Helpers
 * ================================================================== */

/** Permissive CORS, because browser-based public clients need it. */
function cors(ctx) {
  ctx.res.header('access-control-allow-origin', ctx.req.headers.origin || '*')
    .header('access-control-allow-headers', 'authorization,content-type,dpop')
    .header('access-control-allow-methods', 'GET,POST,OPTIONS')
    .header('vary', 'origin');
}

/**
 * Deliver an error to the client by redirect -- only ever called AFTER
 * client_id and redirect_uri have both been validated.
 */
function redirectError(ctx, redirectUri, error, description, state) {
  const url = new URL(redirectUri);
  url.searchParams.set('error', error);
  if (description) url.searchParams.set('error_description', description);
  if (state) url.searchParams.set('state', state);
  trace.error(`/authorize rejected: ${error}`, { description, redirect_to: redirectUri });
  return ctx.res.redirect(url.toString());
}

/** OAuth 2.1: exact string comparison. The policy switch exists to show why. */
function redirectUriMatches(client, redirectUri) {
  if (policy.require_exact_redirect_uri) {
    return { ok: client.redirect_uris.includes(redirectUri), mode: 'exact' };
  }
  const ok = client.redirect_uris.some((registered) => redirectUri.startsWith(registered.replace(/\/callback$/, '')));
  return { ok, mode: 'prefix (UNSAFE)' };
}

function authenticateClient(ctx, { allowPublic = true } = {}) {
  const basic = parseBasicAuth(ctx.req);
  const fields = ctx.body.fields || {};
  const clientId = basic ? basic.clientId : fields.client_id;
  const presentedSecret = basic ? basic.clientSecret : fields.client_secret;
  const method = basic ? 'client_secret_basic' : (fields.client_secret ? 'client_secret_post' : 'none');

  if (!clientId) {
    return { ok: false, error: 'invalid_client', description: 'No client_id: send HTTP Basic credentials or a client_id form field.' };
  }
  const client = cfg.findClient(clientId);
  if (!client) {
    return { ok: false, error: 'invalid_client', description: `Unknown client_id "${clientId}".` };
  }

  if (client.client_type === 'confidential') {
    if (!presentedSecret) {
      return { ok: false, error: 'invalid_client', description: `Client "${clientId}" is confidential and must authenticate (${client.token_endpoint_auth_method}).` };
    }
    if (!jose.safeEqual(presentedSecret, client.client_secret)) {
      trace.error('client authentication failed: wrong secret', { client_id: clientId, method });
      return { ok: false, error: 'invalid_client', description: 'Client authentication failed.' };
    }
    trace.ok(`client authenticated via ${method}`, { client_id: clientId });
    return { ok: true, client, method, authenticated: true };
  }

  if (!allowPublic) {
    return { ok: false, error: 'invalid_client', description: `Client "${clientId}" is public and cannot use this grant.` };
  }
  // A public client is *identified*, never *authenticated*. PKCE is what
  // actually proves this is the same app that started the flow.
  trace.info('public client identified (no secret; PKCE carries the proof)', { client_id: clientId });
  return { ok: true, client, method: 'none', authenticated: false };
}

function tokenError(ctx, error, description, status = 400, extra = {}) {
  trace.error(`/token rejected: ${error}`, { description, ...extra });
  // RFC 6749 §5.2: invalid_client answers 401 and may carry WWW-Authenticate.
  if (error === 'invalid_client') ctx.res.header('www-authenticate', 'Basic realm="oauth-simulator"');
  return ctx.res.json({ error, error_description: description, ...extra }, status);
}

/** Mint a signed JWT access token. */
function issueAccessToken({ client, sub, scope, audience, familyId, authTime }) {
  const issuedAt = now();
  const jti = jose.randomId(12);
  const payload = {
    iss: cfg.ISSUER,
    sub,
    aud: policy.enforce_audience ? audience : undefined,
    client_id: client.client_id,
    scope: scope.join(' '),
    jti,
    iat: issuedAt,
    nbf: issuedAt,
    exp: issuedAt + cfg.LIFETIMES.access_token_sec,
    auth_time: authTime,
  };
  Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);
  // `typ: at+jwt` (RFC 9068) tells an API "this is an access token", so an
  // ID token can never be swapped in where an access token was expected.
  const token = jose.signJwt(payload, signingKey, { typ: 'at+jwt' });
  store.recordAccessToken(jti, { clientId: client.client_id, sub, scope: scope.join(' '), familyId, exp: payload.exp });
  trace.ok('access token issued', { sub, client_id: client.client_id, scope: payload.scope, aud: payload.aud, expires_in: cfg.LIFETIMES.access_token_sec, jti });
  return { token, payload };
}

/** Mint an OIDC ID token: a statement about *who logged in*, for the client. */
function issueIdToken({ client, user, nonce, authTime, scope }) {
  const issuedAt = now();
  const payload = {
    iss: cfg.ISSUER,
    sub: user.sub,
    aud: client.client_id,   // note: audience is the CLIENT, not the API
    iat: issuedAt,
    exp: issuedAt + cfg.LIFETIMES.id_token_sec,
    auth_time: authTime,
    nonce: nonce || undefined,
    ...(scope.includes('profile') ? { name: user.name, preferred_username: user.username } : {}),
    ...(scope.includes('email') ? { email: user.email, email_verified: user.email_verified } : {}),
  };
  Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);
  trace.ok('ID token issued', { sub: user.sub, aud: client.client_id, nonce_echoed: Boolean(nonce) });
  return jose.signJwt(payload, signingKey, { typ: 'JWT' });
}

function resolveAudience(client, requestedResource) {
  if (requestedResource) return requestedResource;             // RFC 8707
  if (client.audience && client.audience.length === 1) return client.audience[0];
  return client.audience || [];
}

/* ================================================================== *
 * Discovery + keys
 * ================================================================== */
function metadata() {
  return {
    issuer: cfg.ISSUER,
    authorization_endpoint: `${cfg.ISSUER}/authorize`,
    token_endpoint: `${cfg.ISSUER}/token`,
    userinfo_endpoint: `${cfg.ISSUER}/userinfo`,
    jwks_uri: `${cfg.ISSUER}/jwks.json`,
    introspection_endpoint: `${cfg.ISSUER}/introspect`,
    revocation_endpoint: `${cfg.ISSUER}/revoke`,
    end_session_endpoint: `${cfg.ISSUER}/logout`,
    scopes_supported: ALL_SCOPES,
    // OAuth 2.1: `code` only. No `token`, no `id_token token` -- the implicit
    // grant is gone, so no response type can return a token in the front channel.
    response_types_supported: policy.allow_legacy_grants ? ['code', 'token'] : ['code'],
    response_modes_supported: ['query'],
    grant_types_supported: policy.allow_legacy_grants
      ? ['authorization_code', 'refresh_token', 'client_credentials', 'password', 'implicit']
      : ['authorization_code', 'refresh_token', 'client_credentials'],
    subject_types_supported: ['public'],
    id_token_signing_alg_values_supported: ['RS256'],
    token_endpoint_auth_methods_supported: ['client_secret_basic', 'client_secret_post', 'none'],
    // The two lines that make this an OAuth 2.1 server: S256 is offered, and
    // PKCE is declared required.
    code_challenge_methods_supported: policy.allow_plain_code_challenge ? ['S256', 'plain'] : ['S256'],
    require_pushed_authorization_requests: false,
    claims_supported: ['sub', 'name', 'preferred_username', 'email', 'email_verified', 'auth_time'],
    service_documentation: 'http://localhost:9020/concepts',
    'x-simulator': {
      note: 'Teaching simulator. Signing keys are ephemeral; all state is in memory.',
      policy,
      pkce_required: policy.require_pkce,
    },
  };
}

app.get('/.well-known/oauth-authorization-server', (ctx) => { cors(ctx); trace.info('discovery document served (RFC 8414)'); return ctx.res.json(metadata()); });
app.get('/.well-known/openid-configuration', (ctx) => { cors(ctx); trace.info('OIDC discovery document served'); return ctx.res.json(metadata()); });
app.options('/.well-known/oauth-authorization-server', (ctx) => { cors(ctx); return ctx.res.text('', 204); });
app.get('/jwks.json', (ctx) => {
  cors(ctx);
  trace.info('JWKS served: any API can now verify our signatures offline', { kid: publicJwk.kid });
  return ctx.res.json({ keys: [publicJwk] });
});
app.get('/healthz', (ctx) => ctx.res.json({ status: 'ok', service: 'authorization-server', issuer: cfg.ISSUER, kid: publicJwk.kid }));

/* ================================================================== *
 * /authorize -- the FRONT CHANNEL
 *
 * Runs in the user's browser. Everything here is visible in the URL bar, in
 * history and in Referer headers, so nothing secret may ever travel on it.
 * Its only job is to produce a short-lived, single-use authorization code.
 * ================================================================== */
function handleAuthorize(ctx) {
  const q = ctx.query;
  trace.step('GET /authorize received', {
    client_id: q.client_id, response_type: q.response_type, scope: q.scope,
    redirect_uri: q.redirect_uri, has_code_challenge: Boolean(q.code_challenge),
    code_challenge_method: q.code_challenge_method, has_state: Boolean(q.state),
  });

  /* --- Step 1: identify the client. Not yet trusted, just recognised. --- */
  const client = cfg.findClient(q.client_id);
  if (!client) {
    trace.error('unknown client_id -- refusing to redirect anywhere', { client_id: q.client_id });
    return ctx.res.html(views.authorizeErrorPage({
      error: 'invalid_client',
      description: `No client is registered with client_id "${q.client_id || '(missing)'}".`,
      hint: 'The authorization server will not redirect an error for an unknown client, because it has no registered URI it can trust.',
      details: q,
    }), 400);
  }

  /* --- Step 2: validate redirect_uri BEFORE trusting it. --- */
  if (!q.redirect_uri) {
    return ctx.res.html(views.authorizeErrorPage({
      error: 'invalid_request', description: 'redirect_uri is missing.',
      hint: `OAuth 2.1 requires the client to send the redirect_uri and requires the AS to compare it exactly. Registered for this client: ${client.redirect_uris.join(', ') || '(none)'}`,
      details: q,
    }), 400);
  }
  const match = redirectUriMatches(client, q.redirect_uri);
  if (!match.ok) {
    trace.error('redirect_uri does not match registration', { presented: q.redirect_uri, registered: client.redirect_uris, mode: match.mode });
    return ctx.res.html(views.authorizeErrorPage({
      error: 'invalid_request',
      description: `redirect_uri "${q.redirect_uri}" is not registered for client "${client.client_id}".`,
      hint: `Matching mode: <b>${match.mode}</b>. Registered: <span class="mono">${client.redirect_uris.join(' ')}</span>. This check is what stops an attacker from having your authorization code delivered to their own server.`,
      details: q,
    }), 400);
  }
  if (match.mode !== 'exact') {
    trace.warn('redirect_uri accepted by PREFIX match -- policy.require_exact_redirect_uri is off', { presented: q.redirect_uri });
  }
  // From here on the AS may safely report errors to the client by redirect.
  const state = q.state;

  /* --- Step 3: response_type. OAuth 2.1 means `code`, full stop. --- */
  if (q.response_type !== 'code') {
    if ((q.response_type === 'token' || q.response_type === 'id_token token') && policy.allow_legacy_grants) {
      trace.warn('IMPLICIT GRANT -- returning a token in the URL fragment. This is what OAuth 2.1 removed.', { response_type: q.response_type });
      return runImplicitGrant(ctx, client, q);
    }
    return redirectError(ctx, q.redirect_uri, 'unsupported_response_type',
      `response_type="${q.response_type || '(missing)'}" is not supported. OAuth 2.1 supports only "code": the implicit grant was removed because it delivered access tokens through the browser URL.`, state);
  }

  /* --- Step 4: PKCE. The defining requirement of OAuth 2.1. --- */
  // The server-wide policy is the master switch -- Lab 03 turns it off to show
  // the pre-PKCE world. A client registration can be exempted only if it never
  // uses authorization codes at all (demo-service); no code-grant client may
  // opt out while the policy is on.
  const pkceRequired = policy.require_pkce && client.require_pkce !== false;
  if (!q.code_challenge) {
    if (pkceRequired) {
      return redirectError(ctx, q.redirect_uri, 'invalid_request',
        'code_challenge is required. OAuth 2.1 mandates PKCE for every authorization code request, for public and confidential clients alike.', state);
    }
    trace.warn('PROCEEDING WITHOUT PKCE -- policy.require_pkce is off. This authorization code is now stealable.', { client_id: client.client_id });
  }
  let challengeMethod = q.code_challenge_method;
  if (q.code_challenge) {
    if (!challengeMethod) {
      // RFC 7636 defaults the method to `plain` when omitted, which is a trap:
      // OAuth 2.1 servers should insist the client says S256 explicitly.
      return redirectError(ctx, q.redirect_uri, 'invalid_request',
        'code_challenge_method must be sent explicitly as "S256". If omitted, RFC 7636 defaults to "plain", which offers no protection.', state);
    }
    if (challengeMethod === 'plain' && !policy.allow_plain_code_challenge) {
      return redirectError(ctx, q.redirect_uri, 'invalid_request',
        'code_challenge_method="plain" is not accepted. With plain, the challenge IS the verifier, so anyone who sees the authorization request can redeem the code.', state);
    }
    if (!['S256', 'plain'].includes(challengeMethod)) {
      return redirectError(ctx, q.redirect_uri, 'invalid_request', `Unknown code_challenge_method "${challengeMethod}".`, state);
    }
    if (challengeMethod === 'S256' && !/^[A-Za-z0-9\-_]{43}$/.test(q.code_challenge)) {
      return redirectError(ctx, q.redirect_uri, 'invalid_request',
        'An S256 code_challenge must be 43 base64url characters (a SHA-256 digest, unpadded). Did you send the verifier by mistake?', state);
    }
    if (challengeMethod === 'plain') {
      trace.warn('code_challenge_method=plain accepted -- the challenge equals the verifier, so PKCE provides no protection here', {});
    } else {
      trace.ok('PKCE challenge accepted and bound to this request', { code_challenge: q.code_challenge, code_challenge_method: 'S256' });
    }
  }

  /* --- Step 5: state --- */
  if (!q.state && policy.require_state) {
    return redirectError(ctx, q.redirect_uri, 'invalid_request',
      'state is required by this server. It lets the client detect a callback it did not initiate and carry its own UI state across the redirect.', state);
  }
  if (!q.state) trace.warn('no state parameter -- the client cannot tie this callback to a request it started', {});

  /* --- Step 6: scope --- */
  const requestedScopes = splitScope(q.scope);
  if (!requestedScopes.length) {
    return redirectError(ctx, q.redirect_uri, 'invalid_scope', 'No scope requested. Ask for the least you need.', state);
  }
  const unknown = requestedScopes.filter((s) => !ALL_SCOPES.includes(s));
  if (unknown.length) {
    return redirectError(ctx, q.redirect_uri, 'invalid_scope', `Unknown scope(s): ${unknown.join(' ')}. Known: ${ALL_SCOPES.join(' ')}.`, state);
  }
  const notAllowedForClient = requestedScopes.filter((s) => !client.allowed_scopes.includes(s));
  if (notAllowedForClient.length) {
    return redirectError(ctx, q.redirect_uri, 'invalid_scope',
      `Client "${client.client_id}" is not registered for scope(s): ${notAllowedForClient.join(' ')}. Scope is capped by registration first, then by the user's own entitlements at consent.`, state);
  }

  /* --- Step 7: park the request and hand over to the IdP. --- */
  const requestId = store.createRequest({
    client_id: client.client_id,
    redirect_uri: q.redirect_uri,
    scope: q.scope,
    state: q.state,
    nonce: q.nonce,
    codeChallenge: q.code_challenge || null,
    codeChallengeMethod: q.code_challenge ? (challengeMethod || 'plain') : null,
    resource: q.resource || null,
    prompt: q.prompt || null,
    stage: 'authenticating',
  });
  trace.info('authorization request parked pending user authentication', { request_id: requestId, scope: q.scope });
  return continueAuthorization(ctx, requestId);
}
app.get('/authorize', handleAuthorize);
app.get('/authorize/resume', (ctx) => continueAuthorization(ctx, ctx.query.request_id));

/**
 * Decide what the browser needs next: log in, consent, or the code.
 * This is also where single sign-on happens -- an existing IdP session means
 * a second application never sees a login form.
 */
function continueAuthorization(ctx, requestId) {
  const request = store.getRequest(requestId);
  if (!request) {
    return ctx.res.html(views.authorizeErrorPage({
      error: 'invalid_request',
      description: 'This authorization request has expired or was already completed.',
      hint: 'Start again from the client application.',
    }), 400);
  }
  const client = cfg.findClient(request.client_id);
  const session = store.getSession(ctx.cookies[SESSION_COOKIE]);
  const requestedScopes = splitScope(request.scope);

  if (!session || request.prompt === 'login') {
    request.stage = 'authenticating';
    trace.info(session ? 'prompt=login: forcing re-authentication' : 'no IdP session: showing the login form');
    return ctx.res.html(views.loginPage({ requestId, client, scopes: requestedScopes }));
  }

  if (request.legacyImplicit) {
    // Only reachable with policy.allow_legacy_grants on (Lab 07).
    store.deleteRequest(requestId);
    return runImplicitGrant(ctx, client, { ...request, redirect_uri: request.redirect_uri });
  }

  const user = cfg.findUserBySub(session.sub);
  request.stage = 'consenting';
  trace.ok('IdP session found -- user is already authenticated (single sign-on)', { sub: user.sub, auth_time: session.authTime });

  const grantable = requestedScopes.filter((s) => user.entitlements.includes(s));
  const refused = requestedScopes.filter((s) => !user.entitlements.includes(s));
  const existing = store.getGrant(client.client_id, user.sub);

  // A real AS skips consent when everything asked for was already granted.
  if (existing && request.prompt !== 'consent' && grantable.every((s) => existing.scopes.includes(s))) {
    trace.info('consent already on file for these scopes -- but the simulator shows the screen anyway so you can see it', { granted: existing.scopes });
  }
  return ctx.res.html(views.consentPage({
    requestId, client, user,
    requestedScopes, grantableScopes: grantable, refusedScopes: refused,
    alreadyGranted: existing ? existing.scopes : [],
  }));
}

/* ---------------- IdP: login ---------------- */
app.post('/login', (ctx) => {
  const { request_id: requestId, username, password, action } = ctx.body.fields;
  const request = store.getRequest(requestId);
  if (!request) return ctx.res.html(views.authorizeErrorPage({ error: 'invalid_request', description: 'Login session expired. Start again from the client.' }), 400);
  const client = cfg.findClient(request.client_id);

  if (action === 'deny') {
    trace.warn('user cancelled at the login screen', {});
    store.deleteRequest(requestId);
    return redirectError(ctx, request.redirect_uri, 'access_denied', 'The user cancelled before signing in.', request.state);
  }

  const user = cfg.findUser(username);
  // One generic message for both wrong-user and wrong-password: telling the
  // difference is a free user-enumeration oracle for an attacker.
  if (!user || !jose.safeEqual(password, user.password)) {
    trace.error('authentication failed', { username });
    return ctx.res.html(views.loginPage({
      requestId, client, scopes: splitScope(request.scope),
      error: 'That username and password combination is not correct.', prefill: username,
    }), 401);
  }

  const sessionId = store.createSession(user.sub, cfg.LIFETIMES.session_sec);
  trace.ok('user authenticated by the IdP; browser session created', { sub: user.sub, username: user.username });
  ctx.res.cookie(SESSION_COOKIE, sessionId, { maxAge: cfg.LIFETIMES.session_sec, sameSite: 'Lax' });
  return ctx.res.redirect(`/authorize/resume?request_id=${encodeURIComponent(requestId)}`);
});

/* ---------------- OAuth: consent, then the code ---------------- */
app.post('/consent', (ctx) => {
  const fields = ctx.body.fields;
  const requestId = fields.request_id;
  const request = store.getRequest(requestId);
  if (!request) return ctx.res.html(views.authorizeErrorPage({ error: 'invalid_request', description: 'Consent session expired. Start again from the client.' }), 400);

  const client = cfg.findClient(request.client_id);
  const session = store.getSession(ctx.cookies[SESSION_COOKIE]);
  if (!session) return continueAuthorization(ctx, requestId);
  const user = cfg.findUserBySub(session.sub);

  if (fields.action === 'deny') {
    trace.warn('user DENIED consent', { sub: user.sub, client_id: client.client_id });
    store.deleteRequest(requestId);
    return redirectError(ctx, request.redirect_uri, 'access_denied',
      'The resource owner refused the request. This is a normal outcome and every client must handle it.', request.state);
  }

  // Checkbox forms send one value per checked box; normalise to an array.
  const raw = ctx.body.raw;
  const checked = new URLSearchParams(raw).getAll('scope');
  const requestedScopes = splitScope(request.scope);
  // Approved scope = asked for AND user is entitled to AND user ticked it.
  // `openid` is disabled in the form (so not submitted) but implied when asked for.
  const approved = requestedScopes.filter((s) =>
    user.entitlements.includes(s) && (checked.includes(s) || s === 'openid'));

  if (!approved.length) {
    store.deleteRequest(requestId);
    return redirectError(ctx, request.redirect_uri, 'access_denied', 'The user granted no scopes.', request.state);
  }
  const dropped = requestedScopes.filter((s) => !approved.includes(s));
  if (dropped.length) trace.warn('granted scope is NARROWER than requested', { requested: requestedScopes, granted: approved, dropped });

  store.rememberGrant(client.client_id, user.sub, approved);

  /* Mint the authorization code and bind to it everything the token
     endpoint will re-check: the client, the redirect_uri, and the PKCE
     challenge. The code itself is opaque and means nothing on its own. */
  const code = store.createCode({
    clientId: client.client_id,
    sub: user.sub,
    scope: approved,
    redirectUri: request.redirect_uri,
    codeChallenge: request.codeChallenge,
    codeChallengeMethod: request.codeChallengeMethod,
    nonce: request.nonce,
    authTime: session.authTime,
    audience: resolveAudience(client, request.resource),
  }, cfg.LIFETIMES.authorization_code_sec);

  store.deleteRequest(requestId);
  trace.ok('authorization code issued', {
    code_prefix: `${code.slice(0, 10)}...`, sub: user.sub, client_id: client.client_id,
    scope: approved.join(' '), bound_to_challenge: request.codeChallenge ? `${request.codeChallenge.slice(0, 12)}...` : null,
    expires_in: cfg.LIFETIMES.authorization_code_sec,
  });

  const url = new URL(request.redirect_uri);
  url.searchParams.set('code', code);
  if (request.state) url.searchParams.set('state', request.state);
  // RFC 9207: naming the issuer in the response stops a mix-up attack where a
  // code from a malicious AS is replayed at an honest one.
  url.searchParams.set('iss', cfg.ISSUER);
  return ctx.res.redirect(url.toString());
});

app.get('/logout', (ctx) => {
  const session = store.getSession(ctx.cookies[SESSION_COOKIE]);
  if (session) {
    store.endSession(session.id);
    trace.info('IdP session ended', { sub: session.sub });
  }
  ctx.res.clearCookie(SESSION_COOKIE);
  const target = ctx.query.return || '/';
  return ctx.res.redirect(target.startsWith('/') || target.startsWith('http://localhost:9020') ? target : '/');
});

/** The implicit grant, kept only so you can see what was wrong with it. */
function runImplicitGrant(ctx, client, q) {
  const session = store.getSession(ctx.cookies[SESSION_COOKIE]);
  if (!session) {
    const requestId = store.createRequest({ ...q, client_id: client.client_id, redirect_uri: q.redirect_uri, stage: 'authenticating', legacyImplicit: true });
    return ctx.res.html(views.loginPage({ requestId, client, scopes: splitScope(q.scope) }));
  }
  const user = cfg.findUserBySub(session.sub);
  const scope = splitScope(q.scope).filter((s) => user.entitlements.includes(s) && client.allowed_scopes.includes(s));
  const { token } = issueAccessToken({ client, sub: user.sub, scope, audience: resolveAudience(client, q.resource), authTime: session.authTime });
  const fragment = new URLSearchParams({ access_token: token, token_type: 'Bearer', expires_in: String(cfg.LIFETIMES.access_token_sec), scope: scope.join(' '), ...(q.state ? { state: q.state } : {}) });
  trace.error('implicit grant completed: an access token is now in the browser URL fragment, in history, and in any Referer header', {});
  return ctx.res.redirect(`${q.redirect_uri}#${fragment.toString()}`);
}

/* ================================================================== *
 * /token -- the BACK CHANNEL
 *
 * Server-to-server, over TLS in production, never visible to the browser.
 * This is where secrets and proofs are allowed to travel.
 * ================================================================== */
app.options('/token', (ctx) => { cors(ctx); return ctx.res.text('', 204); });
app.post('/token', async (ctx) => {
  cors(ctx);
  const fields = ctx.body.fields || {};
  const grantType = fields.grant_type;
  trace.step('POST /token received', {
    grant_type: grantType,
    client_id: fields.client_id || (parseBasicAuth(ctx.req) || {}).clientId,
    auth: parseBasicAuth(ctx.req) ? 'Basic header' : (fields.client_secret ? 'form secret' : 'none'),
    has_code_verifier: Boolean(fields.code_verifier),
  });

  if (!grantType) return tokenError(ctx, 'invalid_request', 'grant_type is required.');

  /* Grants OAuth 2.1 deleted. */
  if (grantType === 'password' && !policy.allow_legacy_grants) {
    return tokenError(ctx, 'unsupported_grant_type',
      'The resource owner password credentials grant was removed in OAuth 2.1. It requires the application to handle the user\'s password, which defeats the purpose of OAuth, breaks MFA and federation, and cannot be consented to.');
  }
  if (grantType === 'implicit') {
    return tokenError(ctx, 'unsupported_grant_type', 'The implicit grant was removed in OAuth 2.1 and was never a token-endpoint grant type. Use authorization_code + PKCE.');
  }

  const auth = authenticateClient(ctx, { allowPublic: grantType !== 'client_credentials' });
  if (!auth.ok) return tokenError(ctx, auth.error, auth.description, 401);
  const { client } = auth;

  if (!client.grant_types.includes(grantType)) {
    return tokenError(ctx, 'unauthorized_client', `Client "${client.client_id}" is not registered for grant_type "${grantType}". Registered: ${client.grant_types.join(', ')}.`);
  }

  if (grantType === 'authorization_code') return grantAuthorizationCode(ctx, client, fields, auth);
  if (grantType === 'refresh_token') return grantRefreshToken(ctx, client, fields);
  if (grantType === 'client_credentials') return grantClientCredentials(ctx, client, fields);
  if (grantType === 'password') return grantPasswordLegacy(ctx, client, fields);
  return tokenError(ctx, 'unsupported_grant_type', `Unknown grant_type "${grantType}".`);
});

function grantAuthorizationCode(ctx, client, fields, auth) {
  const { code, redirect_uri: redirectUri, code_verifier: codeVerifier } = fields;
  if (!code) return tokenError(ctx, 'invalid_request', 'code is required.');

  const entry = store.getCode(code);
  if (!entry) {
    return tokenError(ctx, 'invalid_grant', 'Unknown authorization code. It may already have been redeemed and deleted, or it never existed.');
  }

  /* --- Replay detection. A code is a one-shot credential. --- */
  if (entry.used) {
    if (policy.single_use_codes) {
      // RFC 9700 §4.1.3: on replay, revoke everything the code produced.
      const killed = entry.familyId ? store.revokeFamily(entry.familyId) : 0;
      for (const [jti, meta] of store.issuedAccessTokens) {
        if (meta.codeId === code) store.revokeJti(jti);
      }
      trace.error('AUTHORIZATION CODE REPLAY DETECTED -- revoking every token this code produced', {
        client_id: client.client_id, originally_redeemed_at: entry.usedAt, tokens_revoked: killed,
      });
      return tokenError(ctx, 'invalid_grant',
        'This authorization code has already been used. Codes are single-use; a replay means someone else may have the code, so every token derived from it has been revoked.');
    }
    trace.error('CODE REPLAY ALLOWED -- policy.single_use_codes is off. Whoever holds this code can keep minting tokens.', {});
  }

  if (entry.expiresAt < now()) {
    store.deleteCode(code);
    return tokenError(ctx, 'invalid_grant', `Authorization code expired ${now() - entry.expiresAt}s ago. Codes live ~${cfg.LIFETIMES.authorization_code_sec}s because they travel through the browser.`);
  }

  /* --- The code belongs to the client it was issued to. --- */
  if (entry.clientId !== client.client_id) {
    trace.error('CODE SUBSTITUTION BLOCKED: a different client tried to redeem this code', { issued_to: entry.clientId, presented_by: client.client_id });
    return tokenError(ctx, 'invalid_grant',
      `This code was issued to "${entry.clientId}" but is being redeemed by "${client.client_id}". A code is bound to one client.`);
  }

  /* --- The same redirect_uri must come back (RFC 6749 §4.1.3). --- */
  if (entry.redirectUri && redirectUri !== entry.redirectUri) {
    return tokenError(ctx, 'invalid_grant',
      `redirect_uri mismatch: the code was issued for "${entry.redirectUri}" but the token request says "${redirectUri || '(missing)'}". Both halves of the flow must agree.`);
  }

  /* --- PKCE: prove you are the app that started this flow. --- */
  if (entry.codeChallenge) {
    const result = jose.verifyPkce({
      codeVerifier, codeChallenge: entry.codeChallenge, codeChallengeMethod: entry.codeChallengeMethod,
    });
    if (!result.ok) {
      store.markCodeUsed(code);   // burn it: something is wrong
      trace.error(`PKCE VERIFICATION FAILED (${result.reason})`, {
        detail: result.detail, expected_challenge: entry.codeChallenge, computed_from_verifier: result.computed || null,
      });
      return tokenError(ctx, 'invalid_grant', `PKCE check failed: ${result.detail}`, 400, {
        'x-simulator-pkce': {
          stored_code_challenge: entry.codeChallenge,
          code_challenge_method: entry.codeChallengeMethod,
          presented_code_verifier: codeVerifier ? `${codeVerifier.slice(0, 12)}...` : null,
          computed_challenge: result.computed || null,
          explanation: 'The AS hashes the presented verifier and compares it to the challenge it stored at /authorize. Only the app that generated the verifier can pass.',
        },
      });
    }
    trace.ok('PKCE verified: SHA-256(code_verifier) equals the stored code_challenge', {
      code_challenge: entry.codeChallenge, method: entry.codeChallengeMethod,
    });
  } else {
    trace.warn('no PKCE binding on this code -- anyone holding the code could have redeemed it', { client_id: client.client_id });
  }

  store.markCodeUsed(code);
  const user = cfg.findUserBySub(entry.sub);
  const scope = entry.scope;
  const audience = entry.audience;

  const familyId = entry.familyId || jose.randomId(8);
  entry.familyId = familyId;   // so a later replay of this code can revoke exactly what it produced
  const { token: accessToken, payload } = issueAccessToken({
    client, sub: entry.sub, scope, audience, familyId, authTime: entry.authTime,
  });
  store.recordAccessToken(payload.jti, { clientId: client.client_id, sub: entry.sub, scope: scope.join(' '), familyId, codeId: code, exp: payload.exp });

  const response = {
    access_token: accessToken,
    token_type: 'Bearer',
    expires_in: cfg.LIFETIMES.access_token_sec,
    scope: scope.join(' '),
  };

  if (client.grant_types.includes('refresh_token')) {
    const rt = store.createRefreshToken({
      clientId: client.client_id, sub: entry.sub, scope, familyId, generation: 1,
      ttlSec: cfg.LIFETIMES.refresh_token_sec, audience,
    });
    response.refresh_token = rt.token;
    trace.ok('refresh token issued', { family: rt.familyId, generation: 1, expires_in: cfg.LIFETIMES.refresh_token_sec });
  }

  if (scope.includes('openid')) {
    response.id_token = issueIdToken({ client, user, nonce: entry.nonce, authTime: entry.authTime, scope });
  }

  trace.ok('code exchanged for tokens', { client_id: client.client_id, sub: entry.sub, scope: response.scope, public_client: !auth.authenticated });
  return ctx.res.json(response);
}

function grantRefreshToken(ctx, client, fields) {
  const presented = fields.refresh_token;
  if (!presented) return tokenError(ctx, 'invalid_request', 'refresh_token is required.');

  const entry = store.getRefreshToken(presented);
  if (!entry) return tokenError(ctx, 'invalid_grant', 'Unknown refresh token. Refresh tokens are stored hashed, so an unknown value is simply rejected.');

  if (entry.clientId !== client.client_id) {
    trace.error('refresh token presented by the wrong client', { issued_to: entry.clientId, presented_by: client.client_id });
    return tokenError(ctx, 'invalid_grant', 'This refresh token belongs to a different client.');
  }
  if (entry.revoked) {
    return tokenError(ctx, 'invalid_grant', 'This refresh token has been revoked, most likely because a replay was detected earlier in its family.');
  }

  /* --- Reuse detection: the whole point of rotation. --- */
  if (entry.used) {
    if (policy.detect_refresh_reuse) {
      const killed = store.revokeFamily(entry.familyId);
      trace.error('REFRESH TOKEN REUSE DETECTED -- revoking the entire token family', {
        family: entry.familyId, generation: entry.generation, tokens_revoked: killed,
      });
      return tokenError(ctx, 'invalid_grant',
        `This refresh token was already used (generation ${entry.generation}). Either it was stolen and replayed, or the legitimate client lost the rotated value. Either way the whole family is now revoked and the user must sign in again. This is RFC 9700 §4.14.`);
    }
    trace.warn('reused refresh token ACCEPTED -- policy.detect_refresh_reuse is off. A stolen refresh token now works forever.', { family: entry.familyId });
  }
  if (entry.expiresAt < now()) {
    return tokenError(ctx, 'invalid_grant', 'Refresh token expired. The user must authenticate again.');
  }

  /* --- Scope may only ever shrink on refresh. --- */
  let scope = entry.scope;
  if (fields.scope) {
    const requested = splitScope(fields.scope);
    const escalation = requested.filter((s) => !entry.scope.includes(s));
    if (escalation.length) {
      return tokenError(ctx, 'invalid_scope',
        `Cannot widen scope on refresh: ${escalation.join(' ')} was never granted. A refresh token can only ever produce the same scope or less.`);
    }
    scope = requested;
    trace.info('client requested a narrower scope on refresh', { granted: entry.scope, now: scope });
  }

  store.updateRefreshToken(entry.hash, { used: true });

  const { token: accessToken } = issueAccessToken({
    client, sub: entry.sub, scope, audience: entry.audience, familyId: entry.familyId, authTime: now(),
  });
  const response = { access_token: accessToken, token_type: 'Bearer', expires_in: cfg.LIFETIMES.access_token_sec, scope: scope.join(' ') };

  if (policy.rotate_refresh_tokens) {
    const rotated = store.createRefreshToken({
      clientId: client.client_id, sub: entry.sub, scope, familyId: entry.familyId,
      generation: entry.generation + 1, ttlSec: cfg.LIFETIMES.refresh_token_sec, audience: entry.audience,
    });
    response.refresh_token = rotated.token;
    trace.ok('refresh token ROTATED: the old value is now dead, this new one replaces it', {
      family: entry.familyId, generation: rotated.generation,
    });
  } else {
    store.updateRefreshToken(entry.hash, { used: false });
    response.refresh_token = presented;
    trace.warn('refresh token NOT rotated -- policy.rotate_refresh_tokens is off; the same long-lived value keeps being reused', {});
  }
  return ctx.res.json(response);
}

function grantClientCredentials(ctx, client, fields) {
  const requested = fields.scope ? splitScope(fields.scope) : client.allowed_scopes;
  const notAllowed = requested.filter((s) => !client.allowed_scopes.includes(s));
  if (notAllowed.length) return tokenError(ctx, 'invalid_scope', `Not registered for scope(s): ${notAllowed.join(' ')}.`);

  // No user, so `sub` is the client itself. There is nobody to consent, so
  // this grant must never be used to act on a user's behalf -- and it gets
  // no refresh token, because the client can just ask again.
  const { token } = issueAccessToken({
    client, sub: client.client_id, scope: requested,
    audience: resolveAudience(client, fields.resource), authTime: now(),
  });
  trace.ok('client_credentials: token issued to the application itself, no user involved', { client_id: client.client_id, scope: requested.join(' ') });
  return ctx.res.json({ access_token: token, token_type: 'Bearer', expires_in: cfg.LIFETIMES.access_token_sec, scope: requested.join(' ') });
}

/** Only reachable with policy.allow_legacy_grants on. Shown to be explained. */
function grantPasswordLegacy(ctx, client, fields) {
  const user = cfg.findUser(fields.username);
  if (!user || !jose.safeEqual(fields.password, user.password)) return tokenError(ctx, 'invalid_grant', 'Bad username or password.');
  const scope = splitScope(fields.scope).filter((s) => user.entitlements.includes(s) && client.allowed_scopes.includes(s));
  const { token } = issueAccessToken({ client, sub: user.sub, scope, audience: resolveAudience(client, fields.resource), authTime: now() });
  trace.error('PASSWORD GRANT USED: this application just handled the user\'s real password. No consent screen, no MFA, no federation.', { username: fields.username });
  return ctx.res.json({
    access_token: token, token_type: 'Bearer', expires_in: cfg.LIFETIMES.access_token_sec, scope: scope.join(' '),
    'x-simulator-warning': 'Removed in OAuth 2.1. The client saw the password, so the user cannot tell a legitimate app from a phishing one.',
  });
}

/* ================================================================== *
 * Token lifecycle endpoints
 * ================================================================== */
app.post('/introspect', (ctx) => {
  // RFC 7662. This endpoint must be protected: it answers questions about
  // other people's tokens, so only registered clients/APIs may call it.
  const auth = authenticateClient(ctx, { allowPublic: false });
  if (!auth.ok) return tokenError(ctx, auth.error, auth.description, 401);

  const token = ctx.body.fields.token;
  if (!token) return ctx.res.json({ active: false, error_description: 'token parameter required' });

  const result = jose.verifyJwt(token, {
    getKey: (kid) => (kid === publicJwk.kid ? jose.jwkToPublicKey(publicJwk) : null),
    issuer: cfg.ISSUER,
  });
  if (!result.valid) {
    trace.info('introspection: token is not active', { reason: result.reason });
    return ctx.res.json({ active: false, 'x-simulator-reason': result.reason, 'x-simulator-detail': result.detail });
  }
  if (store.isJtiRevoked(result.payload.jti)) {
    trace.warn('introspection: token is cryptographically valid but was REVOKED', { jti: result.payload.jti });
    return ctx.res.json({ active: false, 'x-simulator-reason': 'revoked', 'x-simulator-detail': 'The signature is fine, but this token id was revoked before its exp. Only introspection can see this -- local JWT validation cannot.' });
  }
  trace.ok('introspection: token is active', { sub: result.payload.sub, jti: result.payload.jti });
  return ctx.res.json({
    active: true, scope: result.payload.scope, client_id: result.payload.client_id,
    sub: result.payload.sub, aud: result.payload.aud, iss: result.payload.iss,
    exp: result.payload.exp, iat: result.payload.iat, jti: result.payload.jti, token_type: 'Bearer',
  });
});

app.post('/revoke', (ctx) => {
  // RFC 7009 always answers 200, even for an unknown token: a revocation
  // endpoint must not become an oracle for guessing valid tokens.
  const auth = authenticateClient(ctx, { allowPublic: true });
  if (!auth.ok) return tokenError(ctx, auth.error, auth.description, 401);
  const token = ctx.body.fields.token;
  const hint = ctx.body.fields.token_type_hint;

  const refresh = store.getRefreshToken(token);
  if (refresh) {
    const killed = store.revokeFamily(refresh.familyId);
    trace.ok('refresh token revoked along with its family', { family: refresh.familyId, tokens: killed });
    return ctx.res.json({ revoked: true, 'x-simulator': `Revoked refresh family ${refresh.familyId} (${killed} tokens) and every access token it produced.` });
  }
  const decoded = jose.decodeJwt(token);
  if (decoded && decoded.payload.jti) {
    store.revokeJti(decoded.payload.jti);
    trace.ok('access token revoked by jti', { jti: decoded.payload.jti, note: 'APIs doing local JWT validation will still accept it until exp' });
    return ctx.res.json({ revoked: true, 'x-simulator': 'Access token jti added to the revocation list. Note: an API that validates JWTs locally will keep accepting it until it expires -- that is the trade-off of self-contained tokens.' });
  }
  trace.info('revocation request for an unknown token -- answering 200 anyway (RFC 7009)', { token_type_hint: hint });
  return ctx.res.json({ revoked: true, 'x-simulator': 'Unknown token. RFC 7009 requires 200 here so that this endpoint cannot be used to test whether a token is valid.' });
});

app.options('/userinfo', (ctx) => { cors(ctx); return ctx.res.text('', 204); });
app.get('/userinfo', (ctx) => {
  cors(ctx);
  const token = bearerToken(ctx.req);
  if (!token) {
    ctx.res.header('www-authenticate', 'Bearer realm="oauth-simulator"');
    return ctx.res.json({ error: 'invalid_token', error_description: 'Bearer access token required.' }, 401);
  }
  const result = jose.verifyJwt(token, {
    getKey: (kid) => (kid === publicJwk.kid ? jose.jwkToPublicKey(publicJwk) : null),
    issuer: cfg.ISSUER,
  });
  if (!result.valid) {
    ctx.res.header('www-authenticate', `Bearer error="invalid_token", error_description="${result.reason}"`);
    return ctx.res.json({ error: 'invalid_token', error_description: result.detail }, 401);
  }
  const scope = splitScope(result.payload.scope);
  if (!scope.includes('openid')) {
    return ctx.res.json({ error: 'insufficient_scope', error_description: 'The openid scope is required to call /userinfo.' }, 403);
  }
  const user = cfg.findUserBySub(result.payload.sub);
  if (!user) return ctx.res.json({ error: 'invalid_token', error_description: 'No such subject (this may be a client_credentials token, which has no user).' }, 401);

  // Claims are filtered by the scopes that were actually granted.
  const claims = { sub: user.sub };
  if (scope.includes('profile')) Object.assign(claims, { name: user.name, preferred_username: user.username });
  if (scope.includes('email')) Object.assign(claims, { email: user.email, email_verified: user.email_verified });
  trace.ok('/userinfo served', { sub: user.sub, scope: result.payload.scope, claims_returned: Object.keys(claims) });
  return ctx.res.json(claims);
});

/* ================================================================== *
 * Simulator extras: policy, state, trace, PKCE calculator
 * ================================================================== */
app.get('/', (ctx) => ctx.res.html(views.homePage({ issuer: cfg.ISSUER, clients: cfg.CLIENTS, policy, users: cfg.USERS })));
app.get('/policy', (ctx) => ctx.res.html(views.policyPage({ policy, defaults: cfg.DEFAULT_POLICY })));
app.post('/policy', (ctx) => {
  const { key, value } = ctx.body.fields;
  if (!(key in policy)) return ctx.res.json({ error: 'unknown_policy_key', key }, 400);
  policy[key] = value === 'true';
  const unsafe = policy[key] !== cfg.DEFAULT_POLICY[key];
  trace[unsafe ? 'warn' : 'ok'](`policy changed: ${key} = ${policy[key]}${unsafe ? '  << this server is now running unsafely' : ''}`, {});
  return ctx.res.redirect('/policy');
});
app.post('/policy/reset', (ctx) => {
  Object.assign(policy, cfg.DEFAULT_POLICY);
  trace.ok('policy restored to safe OAuth 2.1 defaults');
  return ctx.res.redirect('/policy');
});
app.get('/policy.json', (ctx) => { cors(ctx); return ctx.res.json({ policy, defaults: cfg.DEFAULT_POLICY }); });

app.get('/state', (ctx) => ctx.res.html(views.statePage(store.snapshot())));
app.get('/state.json', (ctx) => { cors(ctx); return ctx.res.json(store.snapshot()); });
app.post('/state/reset', (ctx) => { store.reset(); trace.warn('all server state wiped'); return ctx.res.redirect('/state'); });

app.get('/trace', (ctx) => ctx.res.html(views.tracePage()));
app.get('/trace.json', (ctx) => { cors(ctx); return ctx.res.json({ events: trace.all(Number(ctx.query.since || 0)) }); });
app.get('/trace/stream', (ctx) => {
  cors(ctx);
  const stream = ctx.res.sse();
  for (const event of trace.all(Math.max(0, Number(ctx.query.since || 0)))) stream.send('trace', event);
  const unsubscribe = trace.subscribe((event) => stream.send('trace', event));
  stream.onClose(unsubscribe);
});

/** A PKCE calculator, so the transform is never a black box. */
app.get('/pkce/explain', (ctx) => {
  cors(ctx);
  const verifier = ctx.query.verifier || jose.createCodeVerifier();
  return ctx.res.json(jose.explainCodeChallenge(verifier));
});

/* ================================================================== */
app.listen(cfg.PORT, (port) => {
  trace.ok(`authorization server + IdP listening on :${port}`, { issuer: cfg.ISSUER, kid: publicJwk.kid });
  console.log(`\n  Authorization Server  ${cfg.ISSUER}`);
  console.log(`  Discovery             ${cfg.ISSUER}/.well-known/oauth-authorization-server`);
  console.log(`  Policy switches       ${cfg.ISSUER}/policy`);
  console.log(`  Live trace            ${cfg.ISSUER}/trace\n`);
});
