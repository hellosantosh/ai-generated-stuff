'use strict';
/**
 * The OAuth 2.1 client application.
 *
 * This is the side most developers actually write, so it is written the way a
 * correct client must behave:
 *
 *   - the code_verifier is created per-request and kept in server-side session
 *     state, never in a cookie, never in the URL, never in localStorage;
 *   - `state` is random per request and verified on the way back;
 *   - the code is exchanged from the server, over the back channel;
 *   - tokens live server-side; the browser only ever holds a session cookie.
 *
 * Every lab that misbehaves does so through an explicit switch in labs.js, so
 * the correct path stays readable.
 */
const { App, fetchJson } = require('../shared/http');
const { createTracer } = require('../shared/trace');
const jose = require('../shared/jose');
const views = require('./views');
const { LABS, byId } = require('./labs');

const PORT = Number(process.env.PORT || 9020);
const SELF = process.env.SELF_URL || 'http://localhost:9020';
/** Where the *browser* is sent. Must be the AS's public issuer URL. */
const AS_PUBLIC = process.env.AS_PUBLIC_URL || 'http://localhost:9000';
/** Where *this server* calls the AS. Inside Docker that is a service name. */
const AS_INTERNAL = process.env.AS_INTERNAL_URL || AS_PUBLIC;
const RS_INTERNAL = process.env.RS_INTERNAL_URL || 'http://localhost:9010';

const CLIENTS = {
  'demo-web-app': { secret: process.env.DEMO_WEB_APP_SECRET || 'web-app-super-secret', redirect_uri: `${SELF}/callback` },
  'demo-spa': { secret: null, redirect_uri: `${SELF}/spa/callback` },
  'demo-service': { secret: process.env.DEMO_SERVICE_SECRET || 'service-super-secret', redirect_uri: null },
  'evil-app': { secret: null, redirect_uri: `${SELF}/labs/evil/callback` },
};

const trace = createTracer('client');
const app = new App({ name: 'client-app' });
const COOKIE = 'client_app_session';

/* ------------------------------------------------------------------ *
 * Server-side sessions. Tokens must never reach the browser.
 * ------------------------------------------------------------------ */
const sessions = new Map();

function getSession(ctx) {
  let id = ctx.cookies[COOKIE];
  let session = id && sessions.get(id);
  if (!session) {
    id = jose.randomToken(24);
    session = { id, steps: [], tokens: null, pending: null, createdAt: Date.now() };
    sessions.set(id, session);
    ctx.res.cookie(COOKIE, id, { maxAge: 7200, sameSite: 'Lax' });
  }
  return session;
}

function addStep(session, step) {
  session.steps.push({ at: new Date().toISOString().slice(11, 19), ...step });
  if (session.steps.length > 60) session.steps.shift();
  trace.log(step.status === 'fail' ? 'error' : step.status === 'done' ? 'ok' : 'info', step.title, {});
  return step;
}

const nowSec = () => Math.floor(Date.now() / 1000);

async function fetchPolicy() {
  try {
    const { json } = await fetchJson(`${AS_INTERNAL}/policy.json`, { timeoutMs: 2000 });
    return json ? json.policy : null;
  } catch { return null; }
}

/** POST to the AS token endpoint with the right client authentication. */
async function callTokenEndpoint(clientId, params) {
  const client = CLIENTS[clientId];
  const body = new URLSearchParams(params);
  const headers = { 'content-type': 'application/x-www-form-urlencoded' };
  if (client && client.secret) {
    // Confidential client: HTTP Basic, per RFC 6749 §2.3.1.
    headers.authorization = `Basic ${Buffer.from(`${clientId}:${client.secret}`).toString('base64')}`;
  } else {
    // Public client: identify only. There is no secret to send.
    body.set('client_id', clientId);
  }
  const { status, json, text } = await fetchJson(`${AS_INTERNAL}/token`, { method: 'POST', headers, body: body.toString() });
  return { status, body: json || text };
}

function curlForToken(clientId, params) {
  const client = CLIENTS[clientId];
  const lines = [`curl -s -X POST ${AS_PUBLIC}/token \\`];
  if (client && client.secret) lines.push(`  -u '${clientId}:${client.secret}' \\`);
  for (const [k, v] of Object.entries(params)) {
    const shown = k === 'code' || k === 'code_verifier' || k === 'refresh_token' ? v : v;
    lines.push(`  -d '${k}=${shown}' \\`);
  }
  if (!(client && client.secret)) lines.push(`  -d 'client_id=${clientId}' \\`);
  return `${lines.join('\n').replace(/ \\$/, '')}`;
}

/* ================================================================== *
 * Starting a flow
 * ================================================================== */
app.post('/labs/:id/start', (ctx) => {
  const lab = byId(ctx.params.id);
  if (!lab || !lab.start) return ctx.res.redirect('/');
  const session = getSession(ctx);
  session.steps = [];

  const clientId = lab.start.client;
  const client = CLIENTS[clientId];
  const scope = (ctx.body.fields.scope || lab.start.scope).trim();

  /* --- Step 1: PKCE. Create the verifier, derive the challenge. --- */
  const verifier = jose.createCodeVerifier();
  const explained = jose.explainCodeChallenge(verifier);
  const state = lab.start.state ? jose.randomToken(16) : null;
  const nonce = lab.start.nonce ? jose.randomToken(12) : null;

  // Lab 09 asks for a callback URL that was never registered.
  const redirectUri = lab.start.tamperRedirect
    ? `${SELF}/callback/../callback-evil`
    : client.redirect_uri;

  session.pending = { labId: lab.id, clientId, verifier, challenge: explained.code_challenge, state, nonce, scope, redirectUri, startedAt: nowSec() };

  if (lab.start.pkce === 'none') {
    addStep(session, {
      title: 'Skipped PKCE entirely', status: 'warn', highlight: [1],
      why: 'No <span class="mono">code_challenge</span> will be sent. An OAuth 2.1 server must refuse this outright; with <span class="mono">require_pkce</span> turned off it will proceed, and the resulting code is bound to nothing.',
    });
  } else {
    addStep(session, {
      title: 'Generated a code_verifier and derived the code_challenge', status: 'done', highlight: [1],
      request: {
        method: 'GET', url: '(local computation - nothing sent yet)',
        params: {
          code_verifier: explained.code_verifier,
          verifier_length: explained.verifier_length,
          'sha256(verifier) hex': explained.sha256_hex,
          code_challenge: explained.code_challenge,
          code_challenge_method: lab.start.pkce,
        },
        highlight: ['code_verifier', 'code_challenge'],
      },
      why: 'The verifier stays in this server\'s session. Only the hash will cross the browser. Because SHA-256 is one-way, publishing the hash gives an attacker nothing.',
    });
  }

  /* --- Step 2: build the authorization request and redirect. --- */
  const params = new URLSearchParams({ response_type: 'code', client_id: clientId, redirect_uri: redirectUri, scope });
  if (state) params.set('state', state);
  if (nonce) params.set('nonce', nonce);
  if (lab.start.pkce === 'S256') { params.set('code_challenge', explained.code_challenge); params.set('code_challenge_method', 'S256'); }
  if (lab.start.pkce === 'plain') { params.set('code_challenge', verifier); params.set('code_challenge_method', 'plain'); }

  const authorizeUrl = `${AS_PUBLIC}/authorize?${params.toString()}`;
  addStep(session, {
    title: 'Redirecting the browser to the authorization endpoint', status: 'done', highlight: [2, 3],
    request: { method: 'GET', url: authorizeUrl, params: Object.fromEntries(params) },
    why: 'This is the <b>front channel</b>. Everything above is visible in the URL bar and in browser history &mdash; which is exactly why no secret appears in it.',
  });
  trace.step('starting authorization request', { lab: lab.id, client_id: clientId, scope, pkce: lab.start.pkce });
  return ctx.res.redirect(authorizeUrl);
});

/* ================================================================== *
 * The redirect_uri: receiving the authorization code
 * ================================================================== */
async function handleCallback(ctx, expectedClient) {
  const session = getSession(ctx);
  const q = ctx.query;
  const pending = session.pending;

  /* --- The AS may legitimately return an error here. --- */
  if (q.error) {
    addStep(session, {
      title: `Authorization server returned an error: ${q.error}`, status: 'fail', highlight: [5, 6],
      request: { method: 'GET', url: `${ctx.url.pathname}${ctx.url.search}`, params: q },
      why: `<b>${q.error_description || q.error}</b>`,
    });
    return ctx.res.html(views.callbackErrorPage({
      error: q.error,
      description: q.error_description || 'The authorization server refused the request.',
      params: q,
    }), 400);
  }

  if (!pending) {
    addStep(session, { title: 'Callback with no flow in progress — dropped', status: 'fail', highlight: [6], request: { method: 'GET', url: ctx.url.pathname, params: q } });
    return ctx.res.html(views.callbackErrorPage({ error: 'no_flow_in_progress', description: 'This client has no record of starting an authorization request. A callback that arrives out of nowhere must be dropped.', params: q }), 400);
  }

  /* --- state check: is this callback one we started? --- */
  if (pending.state && q.state !== pending.state) {
    addStep(session, {
      title: 'state mismatch — callback rejected', status: 'fail', highlight: [6],
      request: { method: 'GET', url: ctx.url.pathname, params: { received_state: q.state, expected_state: pending.state } },
      why: 'The client started a flow with one <span class="mono">state</span> and got a callback carrying another. Someone else produced this code. Dropping it is mandatory.',
    });
    return ctx.res.html(views.callbackErrorPage({ error: 'state_mismatch', description: 'The state parameter does not match the value this client generated, so this callback was not started by this browser session.', params: q }), 400);
  }

  /* --- iss check (RFC 9207): mix-up defence. --- */
  if (q.iss && q.iss !== AS_PUBLIC) {
    addStep(session, { title: 'iss mismatch — possible mix-up attack', status: 'fail', highlight: [6], request: { method: 'GET', url: ctx.url.pathname, params: q } });
    return ctx.res.html(views.callbackErrorPage({ error: 'issuer_mismatch', description: `The callback claims to come from ${q.iss}, not from the authorization server this flow was started with.`, params: q }), 400);
  }

  if (!q.code) {
    return ctx.res.html(views.callbackErrorPage({ error: 'invalid_request', description: 'No authorization code in the callback.', params: q }), 400);
  }

  addStep(session, {
    title: 'Received the authorization code', status: 'done', highlight: [5, 6],
    request: { method: 'GET', url: `${ctx.url.pathname}${ctx.url.search}`, params: { code: q.code, state: q.state, iss: q.iss }, highlight: ['code'] },
    why: `state matched the value this client generated, and <span class="mono">iss</span> names the expected authorization server. The code is <b>opaque and useless on its own</b> &mdash; it is not a token.`,
  });

  const lab = byId(pending.labId);
  const mode = lab && lab.exchange ? lab.exchange.mode : 'normal';

  /* --- Attack simulations run BEFORE the honest exchange. --- */
  let attackerSucceeded = false;
  if (mode === 'attacker-first') {
    attackerSucceeded = await simulateAttackerExchange(session, pending, q.code);
  }

  /* --- The honest exchange. --- */
  const params = {
    grant_type: 'authorization_code',
    code: q.code,
    redirect_uri: pending.redirectUri,
  };
  if (mode === 'wrong-verifier') {
    params.code_verifier = jose.createCodeVerifier();
    addStep(session, {
      title: 'Deliberately exchanging with the WRONG code_verifier', status: 'warn', highlight: [7],
      why: `Sending a freshly generated verifier instead of the one from step 1. Its hash cannot match the stored challenge.`,
    });
  } else if (lab && lab.start.pkce !== 'none') {
    params.code_verifier = pending.verifier;
  }

  const result = await callTokenEndpoint(pending.clientId, params);
  const ok = result.status === 200 && result.body && result.body.access_token;

  addStep(session, {
    title: ok ? 'Exchanged the code for tokens' : 'Token request refused', status: ok ? 'done' : 'fail',
    highlight: ok ? [7, 8] : [7],
    request: {
      method: 'POST', url: `${AS_PUBLIC}/token`,
      params: { ...params, code_verifier: params.code_verifier ? `${String(params.code_verifier).slice(0, 16)}...` : '(not sent)' },
      curl: curlForToken(pending.clientId, params),
      highlight: ['code_verifier'],
    },
    response: {
      status: result.status,
      body: ok ? {
        ...result.body,
        access_token: `${result.body.access_token.slice(0, 32)}... (${result.body.access_token.length} chars)`,
        refresh_token: result.body.refresh_token ? `${result.body.refresh_token.slice(0, 16)}...` : undefined,
        id_token: result.body.id_token ? `${result.body.id_token.slice(0, 32)}...` : undefined,
      } : result.body,
    },
    why: ok
      ? 'This was the <b>back channel</b>: a direct server-to-server call the browser never saw. The AS re-hashed our <span class="mono">code_verifier</span>, matched it against the challenge from step 3, and only then issued tokens.'
      : `The authorization server refused. Read <span class="mono">error_description</span> above &mdash; it explains exactly which check failed.`,
  });

  if (!ok && mode === 'attacker-first') {
    addStep(session, attackerSucceeded ? {
      title: 'Why the legitimate client also failed \u2014 and why that is the least of your problems',
      status: 'fail',
      why: 'The attacker redeemed the code <b>first</b>, and codes are single-use, so the honest client\'s own exchange was refused as a replay. Think about what the user sees: a broken sign-in. Meanwhile the attacker holds a working access token for their account. Without PKCE, whoever reaches the token endpoint first wins &mdash; and the victim\'s error message is the only hint anything happened. Turn <span class="mono">require_pkce</span> back on at <a href="http://localhost:9000/policy" target="_blank">the policy page</a> and run this lab again.',
    } : {
      title: 'Why the legitimate client also failed', status: 'info',
      why: 'When PKCE verification fails, this authorization server <b>burns the code</b> rather than letting it be retried, so the attacker\'s attempt collaterally killed the real client\'s exchange too. That is the right trade-off: a code that may be in someone else\'s hands must not stay usable, and the honest client can simply start a new flow. The user\'s account was never at risk.',
    });
  }

  if (ok) {
    session.tokens = {
      access_token: result.body.access_token,
      refresh_token: result.body.refresh_token || null,
      id_token: result.body.id_token || null,
      previous_refresh_token: null,
      scope: result.body.scope,
      expires_at: nowSec() + Number(result.body.expires_in || 300),
      clientId: pending.clientId,
    };
    const granted = String(result.body.scope || '').split(/\s+/);
    const requested = pending.scope.split(/\s+/);
    const dropped = requested.filter((s) => !granted.includes(s));
    if (dropped.length) {
      addStep(session, {
        title: 'Granted scope is narrower than requested', status: 'warn',
        request: { method: 'GET', url: '(comparison)', params: { requested: requested.join(' '), granted: granted.join(' '), dropped: dropped.join(' ') } },
        why: 'The user is entitled to say no to individual scopes, and a correct client must degrade gracefully rather than assume it got everything it asked for.',
      });
    }
    /* Immediately use the token, so the whole point is visible. */
    await callApi(session, 'GET', '/api/accounts');
  }

  session.pending = ok ? null : session.pending;
  return ctx.res.redirect('/flow');
}

app.get('/callback', (ctx) => handleCallback(ctx, 'demo-web-app'));
app.get('/spa/callback', (ctx) => handleCallback(ctx, 'demo-spa'));
app.get('/labs/evil/callback', (ctx) => {
  // The attacker's own redirect URI. Reached only if redirect_uri validation
  // is weakened -- which is the lesson.
  const session = getSession(ctx);
  addStep(session, {
    title: 'The ATTACKER received the authorization code', status: 'fail', highlight: [5, 6],
    request: { method: 'GET', url: `/labs/evil/callback${ctx.url.search}`, params: ctx.query },
    why: 'The code was delivered to a URL the legitimate client never registered. This only happens when redirect_uri matching is weakened.',
  });
  return ctx.res.redirect('/flow');
});

/**
 * A thief tries to redeem a code they captured.
 * Runs inside this process purely so the lab is self-contained.
 */
async function simulateAttackerExchange(session, pending, code) {
  addStep(session, {
    title: 'An attacker has captured the authorization code', status: 'warn', highlight: [6],
    request: { method: 'GET', url: '(assume: malicious app, leaked log, or crafted redirect)', params: { stolen_code: code } },
    why: 'Codes cross the browser, so treat a leak as inevitable rather than unthinkable. The question is only whether the leak is <i>useful</i>.',
  });

  // Attempt 1: no verifier at all.
  const attempt1 = await callTokenEndpoint(pending.clientId, {
    grant_type: 'authorization_code', code, redirect_uri: pending.redirectUri,
  });
  const blocked1 = attempt1.status !== 200;
  addStep(session, {
    title: blocked1 ? 'Attacker redemption without a code_verifier: REFUSED' : 'Attacker redemption SUCCEEDED — account compromised',
    status: blocked1 ? 'done' : 'fail', highlight: [7],
    request: { method: 'POST', url: `${AS_PUBLIC}/token`, params: { grant_type: 'authorization_code', code: `${code.slice(0, 12)}...`, code_verifier: '(none - the attacker never had it)' } },
    response: { status: attempt1.status, body: attempt1.body },
    why: blocked1
      ? 'PKCE did its job. The attacker holds the code but cannot prove they started the flow, so the code is worthless.'
      : '<b>Without PKCE the code alone is enough.</b> The attacker now holds a real access token for this user. This is precisely the vulnerability OAuth 2.1 closes by making PKCE mandatory.',
  });

  if (!blocked1) return true;   // the theft worked

  // Attempt 2: invent a verifier and hope.
  const guessed = jose.createCodeVerifier();
  const attempt2 = await callTokenEndpoint(pending.clientId, {
    grant_type: 'authorization_code', code, redirect_uri: pending.redirectUri, code_verifier: guessed,
  });
  addStep(session, {
    title: attempt2.status !== 200 ? 'Attacker guessing a code_verifier: REFUSED' : 'Attacker guessed correctly (astronomically unlikely)',
    status: attempt2.status !== 200 ? 'done' : 'fail', highlight: [7],
    request: { method: 'POST', url: `${AS_PUBLIC}/token`, params: { code_verifier: `${guessed.slice(0, 16)}... (invented)` } },
    response: { status: attempt2.status, body: attempt2.body },
    why: 'The verifier is 256 bits of randomness. Guessing it is not an attack, it is a lottery with no winning ticket. Note that the failed attempt also <b>burned the code</b>, so the legitimate client\'s own exchange will now fail too &mdash; a fail-safe outcome.',
  });
}

/* ================================================================== *
 * Using tokens, and the per-lab actions
 * ================================================================== */
async function callApi(session, method, path, { tokenOverride, label } = {}) {
  const token = tokenOverride !== undefined ? tokenOverride : (session.tokens && session.tokens.access_token);
  const headers = {};
  if (token) headers.authorization = `Bearer ${token}`;
  if (method === 'POST') headers['content-type'] = 'application/x-www-form-urlencoded';

  const { status, json, text } = await fetchJson(`${RS_INTERNAL}${path}`, {
    method, headers, body: method === 'POST' ? 'to=ACC-EXTERNAL&amount=42' : undefined,
  });
  const ok = status >= 200 && status < 300;
  addStep(session, {
    title: label || `${method} ${path}`, status: ok ? 'done' : 'fail', highlight: ok ? [9, 10] : [9],
    request: { method, url: `${RS_INTERNAL}${path}`, params: { Authorization: token ? `Bearer ${String(token).slice(0, 24)}...` : '(none)' } },
    response: { status, body: json || text },
    why: ok
      ? 'The API verified the signature against the AS\'s published public key, checked <span class="mono">iss</span>, <span class="mono">aud</span> and <span class="mono">exp</span>, confirmed the scope covered this route, and only then returned data.'
      : status === 403
        ? 'A <b>403</b> means the token is valid but does not carry the required scope. Refreshing will not help &mdash; the user has to grant more.'
        : 'A <b>401</b> means the token itself was not acceptable. The response says which check failed.',
  });
  return { status, body: json || text };
}

const ACTIONS = {
  async refresh(session) {
    if (!session.tokens || !session.tokens.refresh_token) return;
    const old = session.tokens.refresh_token;
    const params = { grant_type: 'refresh_token', refresh_token: old };
    const result = await callTokenEndpoint(session.tokens.clientId, params);
    const ok = result.status === 200 && result.body.access_token;
    addStep(session, {
      title: ok ? 'Refreshed the access token' : 'Refresh refused', status: ok ? 'done' : 'fail',
      request: { method: 'POST', url: `${AS_PUBLIC}/token`, params: { grant_type: 'refresh_token', refresh_token: `${old.slice(0, 16)}...` }, curl: curlForToken(session.tokens.clientId, params) },
      response: { status: result.status, body: ok ? { ...result.body, access_token: `${result.body.access_token.slice(0, 32)}...`, refresh_token: result.body.refresh_token ? `${result.body.refresh_token.slice(0, 16)}...` : undefined } : result.body },
      why: ok
        ? 'Note that a <b>different</b> refresh token came back. The old value is now retired: each refresh token is single-use. No user interaction was needed &mdash; this is how a session outlives a 5-minute access token.'
        : 'The refresh was refused. If this family was revoked, the only way forward is a fresh authorization &mdash; the user signs in again.',
    });
    if (ok) {
      session.tokens.previous_refresh_token = old;
      session.tokens.refresh_token = result.body.refresh_token || old;
      session.tokens.access_token = result.body.access_token;
      session.tokens.scope = result.body.scope;
      session.tokens.expires_at = nowSec() + Number(result.body.expires_in || 300);
    }
  },

  async 'replay-old-refresh'(session) {
    const stale = session.tokens && session.tokens.previous_refresh_token;
    if (!stale) {
      addStep(session, { title: 'No retired refresh token yet', status: 'info', why: 'Run <b>Refresh the access token</b> first, so there is a rotated-out value to replay.' });
      return;
    }
    const result = await callTokenEndpoint(session.tokens.clientId, { grant_type: 'refresh_token', refresh_token: stale });
    const blocked = result.status !== 200;
    addStep(session, {
      title: blocked ? 'Replay of the retired refresh token: REFUSED, family revoked' : 'Replay ACCEPTED — a stolen refresh token works forever',
      status: blocked ? 'done' : 'fail',
      request: { method: 'POST', url: `${AS_PUBLIC}/token`, params: { refresh_token: `${stale.slice(0, 16)}... (retired generation)` } },
      response: { status: result.status, body: result.body },
      why: blocked
        ? 'Reuse detection: the AS cannot tell whether the thief or the real client is replaying, so it revokes <b>the whole family</b>. The user must sign in again. Verify the damage by pressing refresh again &mdash; the current token is dead too.'
        : 'With <span class="mono">detect_refresh_reuse</span> off, a leaked refresh token is permanent access with no way to notice.',
    });
  },

  async 'call-payments'(session) { await callApi(session, 'POST', '/api/payments', { label: 'POST /api/payments (needs payments:write)' }); },

  async 'send-id-token'(session) {
    if (!session.tokens || !session.tokens.id_token) {
      addStep(session, { title: 'No ID token held', status: 'info', why: 'Request the <span class="mono">openid</span> scope to get one.' });
      return;
    }
    await callApi(session, 'GET', '/api/accounts', {
      tokenOverride: session.tokens.id_token,
      label: 'GET /api/accounts with the ID TOKEN instead of the access token',
    });
    addStep(session, {
      title: 'Why that failed', status: 'info',
      why: 'The ID token\'s <span class="mono">aud</span> is the <b>client_id</b>, and its <span class="mono">typ</span> is <span class="mono">JWT</span> rather than <span class="mono">at+jwt</span>. The API checks both. An ID token is a receipt for the login, not a key to the API &mdash; and an API that accepts one is trusting a token that was never scoped for it.',
    });
  },

  async 'wrong-audience'(session) {
    // RFC 8707: ask for a token aimed at a different API.
    const params = { grant_type: 'client_credentials', scope: 'accounts:read', resource: 'http://localhost:9999' };
    const result = await callTokenEndpoint('demo-service', params);
    if (result.status !== 200) {
      addStep(session, { title: 'Could not mint a foreign-audience token', status: 'info', response: { status: result.status, body: result.body } });
      return;
    }
    addStep(session, {
      title: 'Minted a perfectly valid token for a DIFFERENT API', status: 'warn',
      request: { method: 'POST', url: `${AS_PUBLIC}/token`, params, curl: curlForToken('demo-service', params) },
      response: { status: 200, body: { ...result.body, access_token: `${result.body.access_token.slice(0, 32)}...` } },
      why: 'Its signature is genuine and it has not expired. The only thing wrong with it is <span class="mono">aud</span>.',
    });
    await callApi(session, 'GET', '/api/accounts', {
      tokenOverride: result.body.access_token,
      label: 'GET /api/accounts with a token whose aud is http://localhost:9999',
    });
    addStep(session, {
      title: 'Why audience matters', status: 'info',
      why: 'Without the <span class="mono">aud</span> check, any API that trusts this issuer would accept any token it issued &mdash; so a low-value service could replay its tokens against a high-value one. The audience is what confines a token to one API.',
    });
  },

  async 'forged-callback'(session) {
    addStep(session, {
      title: 'Simulating an injected callback with an unknown state', status: 'info',
      why: `Open this URL yourself: <a class="mono" href="/callback?code=attacker-supplied-code&state=not-the-real-state">/callback?code=attacker-supplied-code&amp;state=not-the-real-state</a> &mdash; the client will refuse it, because it has no record of issuing that state.`,
    });
  },

  async m2m(session) {
    const params = { grant_type: 'client_credentials', scope: 'accounts:read' };
    const result = await callTokenEndpoint('demo-service', params);
    const ok = result.status === 200;
    addStep(session, {
      title: ok ? 'Got a token with client_credentials — no user involved' : 'client_credentials refused',
      status: ok ? 'done' : 'fail',
      request: { method: 'POST', url: `${AS_PUBLIC}/token`, params, curl: curlForToken('demo-service', params) },
      response: { status: result.status, body: ok ? { ...result.body, access_token: `${result.body.access_token.slice(0, 40)}...` } : result.body },
      why: ok ? 'No browser, no redirect, no consent screen, and no refresh token. The decoded <span class="mono">sub</span> is the client_id itself.' : '',
    });
    if (!ok) return;
    const decoded = jose.decodeJwt(result.body.access_token);
    addStep(session, {
      title: 'Inside a machine-to-machine token', status: 'info',
      response: { status: 200, body: decoded.payload },
      why: '<span class="mono">sub == client_id</span>: there is no human here. Calling <span class="mono">/api/me</span> with this token returns 404, because no user owns it. Never use this grant to act on a user\'s behalf.',
    });
    await callApi(session, 'GET', '/api/me', { tokenOverride: result.body.access_token, label: 'GET /api/me with a machine token' });
  },

  async introspect(session) {
    if (!session.tokens) return;
    const body = new URLSearchParams({ token: session.tokens.access_token, token_type_hint: 'access_token' });
    const { status, json } = await fetchJson(`${AS_INTERNAL}/introspect`, {
      method: 'POST',
      headers: {
        'content-type': 'application/x-www-form-urlencoded',
        authorization: `Basic ${Buffer.from(`demo-service:${CLIENTS['demo-service'].secret}`).toString('base64')}`,
      },
      body: body.toString(),
    });
    addStep(session, {
      title: 'Introspected the access token (RFC 7662)', status: json && json.active ? 'done' : 'fail',
      request: { method: 'POST', url: `${AS_PUBLIC}/introspect`, params: { token: `${session.tokens.access_token.slice(0, 24)}...` } },
      response: { status, body: json },
      why: 'Introspection is how a resource server that cannot verify JWTs locally &mdash; or one that must honour revocation immediately &mdash; checks a token. Note that the endpoint requires client authentication: it answers questions about other people\'s tokens.',
    });
  },

  async 'revoke-then-compare'(session) {
    if (!session.tokens) return;
    const token = session.tokens.access_token;
    const body = new URLSearchParams({ token, token_type_hint: 'access_token' });
    const { status, json } = await fetchJson(`${AS_INTERNAL}/revoke`, {
      method: 'POST',
      headers: {
        'content-type': 'application/x-www-form-urlencoded',
        authorization: `Basic ${Buffer.from(`demo-web-app:${CLIENTS['demo-web-app'].secret}`).toString('base64')}`,
      },
      body: body.toString(),
    });
    addStep(session, {
      title: 'Revoked the access token (RFC 7009)', status: 'done',
      request: { method: 'POST', url: `${AS_PUBLIC}/revoke`, params: { token: `${token.slice(0, 24)}...` } },
      response: { status, body: json },
    });
    await callApi(session, 'GET', '/api/accounts', { label: 'GET /api/accounts (API validates the JWT locally)' });
    await callApi(session, 'GET', '/api/accounts-introspected', { label: 'GET /api/accounts-introspected (API asks the AS)' });
    addStep(session, {
      title: 'The trade-off, in two HTTP responses', status: 'info',
      why: 'Local validation <b>still accepted</b> the revoked token, because the signature and <span class="mono">exp</span> are unchanged &mdash; nothing about a self-contained JWT knows it was revoked. Introspection refused it, at the cost of a network round trip on every call. This is exactly why access tokens are given lifetimes measured in minutes.',
    });
  },

  async 'legacy-password'(session) {
    const params = { grant_type: 'password', username: 'alice', password: 'wonderland', scope: 'accounts:read' };
    const result = await callTokenEndpoint('demo-web-app', params);
    const blocked = result.status !== 200;
    addStep(session, {
      title: blocked ? 'Password grant: REFUSED (removed in OAuth 2.1)' : 'Password grant SUCCEEDED — and look what it cost',
      status: blocked ? 'done' : 'fail',
      request: { method: 'POST', url: `${AS_PUBLIC}/token`, params: { ...params, password: '******** (the app has the real password!)' }, curl: curlForToken('demo-web-app', { ...params, password: '********' }) },
      response: { status: result.status, body: result.body },
      why: blocked
        ? 'The application asked for the user\'s password, and OAuth 2.1 has no grant that accepts one. There is nothing to consent to, no MFA prompt possible, no way to federate to another IdP, and a phishing app looks identical to a real one.'
        : 'This client just handled the real password. No consent screen appeared. The user cannot tell this app from a phishing page, and MFA was never invoked.',
    });
  },

  async 'legacy-implicit'(session) {
    const url = `${AS_PUBLIC}/authorize?response_type=token&client_id=demo-spa&redirect_uri=${encodeURIComponent(`${SELF}/spa/callback`)}&scope=accounts:read&state=${jose.randomToken(8)}`;
    const { status, json, headers } = await fetchJson(url, { redirect: 'manual' });
    const location = headers.get('location');
    const blocked = !location || !location.includes('#access_token');
    addStep(session, {
      title: blocked ? 'Implicit grant: REFUSED (removed in OAuth 2.1)' : 'Implicit grant returned a token in the URL fragment',
      status: blocked ? 'done' : 'fail',
      request: { method: 'GET', url, params: { response_type: 'token' } },
      response: { status, body: json || { location: location ? `${location.slice(0, 120)}...` : '(rendered an error page)' } },
      why: blocked
        ? 'OAuth 2.1 supports only <span class="mono">response_type=code</span>. There is no response type that puts a token in the browser URL.'
        : 'That access token is now in the URL fragment: in browser history, in any <span class="mono">Referer</span> header the page emits, and readable by any script on the page. There was also no way to authenticate the client. Authorization Code + PKCE gives the same capability with none of this.',
    });
  },
};

app.post('/labs/:id/action', async (ctx) => {
  const lab = byId(ctx.params.id);
  const session = getSession(ctx);
  const action = ctx.body.fields.action;
  const handler = ACTIONS[action];
  if (!lab || !handler) return ctx.res.redirect('/');
  trace.step(`action: ${action}`, { lab: lab.id });
  await handler(session);
  return ctx.res.redirect(`/labs/${lab.id}?ran=${encodeURIComponent(action)}`);
});

app.post('/api/call', async (ctx) => {
  const session = getSession(ctx);
  const { method, path } = ctx.body.fields;
  const result = await callApi(session, method === 'POST' ? 'POST' : 'GET', path || '/api/accounts');
  session.lastApiResult = result;
  return ctx.res.redirect('/tokens');
});

/* ================================================================== *
 * Pages
 * ================================================================== */
app.get('/', (ctx) => ctx.res.html(views.homePage(getSession(ctx))));
app.get('/concepts', (ctx) => ctx.res.html(views.conceptsPage()));
app.get('/flow', (ctx) => ctx.res.html(views.flowPage(getSession(ctx))));
app.get('/tokens', (ctx) => {
  const session = getSession(ctx);
  const t = session.tokens || {};
  const result = session.lastApiResult;
  session.lastApiResult = null;
  return ctx.res.html(views.tokensPage({
    session,
    decoded: { access: t.access_token ? jose.decodeJwt(t.access_token) : null, id: t.id_token ? jose.decodeJwt(t.id_token) : null },
    apiResult: result,
  }));
});
app.get('/labs/:id', async (ctx) => {
  const lab = byId(ctx.params.id);
  if (!lab) return ctx.res.redirect('/');
  const session = getSession(ctx);
  const policy = lab.policyHint ? await fetchPolicy() : null;
  const ran = ctx.query.ran;
  return ctx.res.html(views.labPage({
    lab, session, policy,
    message: ran ? { kind: '', html: `Action <b class="mono">${ran}</b> completed. The result is in the timeline below and in the <a href="/flow">flow inspector</a>.` } : null,
  }));
});
app.post('/reset', (ctx) => {
  const session = getSession(ctx);
  session.steps = []; session.tokens = null; session.pending = null;
  trace.info('client session cleared');
  return ctx.res.redirect('/flow');
});
app.get('/trace.json', (ctx) => ctx.res.json({ events: trace.all(Number(ctx.query.since || 0)) }));
app.get('/healthz', (ctx) => ctx.res.json({ status: 'ok', service: 'client-app', self: SELF, as: AS_PUBLIC, rs: RS_INTERNAL }));

app.listen(PORT, (port) => {
  trace.ok(`client app listening on :${port}`, { as_public: AS_PUBLIC, as_internal: AS_INTERNAL, rs: RS_INTERNAL });
  console.log(`\n  Client App        ${SELF}`);
  console.log(`  Labs              ${SELF}/`);
  console.log(`  Concepts primer   ${SELF}/concepts\n`);
});
