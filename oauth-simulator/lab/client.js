'use strict';
// ===========================================================================
// LAB FILE 4 of 4 - client.js              run:  node client.js <lab>
// The OAuth 2.1 client, plus every lab scenario. It walks the front channel
// with a cookie jar instead of a browser, so each lab is one command and the
// result is printed, not clicked.
//
//   node client.js list                show every lab
//   node client.js happy               Lab 1  the correct flow
//   node client.js all                 run all of them
// ===========================================================================
const C = require('./crypto-lab');

const AS = 'http://localhost:7000';
const API = 'http://localhost:7010';
const REDIRECT = 'http://localhost:7020/callback';
const CLIENT = { id: 'demo-web-app', secret: 'web-app-secret' };
const SPA = { id: 'demo-spa', secret: null };
const SERVICE = { id: 'demo-service', secret: 'service-secret' };
const AGENT = { id: 'demo-agent', secret: null };
const ORCH = { id: 'demo-orchestrator', secret: 'orchestrator-secret' };
const DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code';
const EXCHANGE_GRANT = 'urn:ietf:params:oauth:grant-type:token-exchange';

// --- printing -------------------------------------------------------------
const B = '\x1b[1m', D = '\x1b[2m', R = '\x1b[0m';
const CY = '\x1b[36m', GR = '\x1b[32m', RE = '\x1b[31m', YE = '\x1b[33m';
const step = (n, s) => console.log('\n' + CY + B + 'STEP ' + n + '  ' + s + R);
const why = (s) => console.log('  ' + D + s + R);
const val = (k, v) => console.log('  ' + k.padEnd(20) + ' ' + v);
const pass = (s) => console.log('  ' + GR + B + 'PASS' + R + ' ' + s);
const fail = (s) => console.log('  ' + RE + B + 'FAIL' + R + ' ' + s);
const note = (s) => console.log('  ' + YE + B + 'NOTE' + R + ' ' + s);
const head = (s) => console.log('\n' + B + '='.repeat(70) + '\n' + s + '\n'
  + '='.repeat(70) + R);

// --- a browser, more or less ---------------------------------------------
// Keeps one cookie and never follows redirects, so we can read the Location
// header the way the browser's address bar would show it.
function makeBrowser() {
  let cookie = null;
  return async function go(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (cookie) headers.cookie = cookie;
    const res = await fetch(url, { ...options, headers, redirect: 'manual' });
    const set = res.headers.get('set-cookie');
    if (set) cookie = set.split(';')[0];
    return { status: res.status, location: res.headers.get('location'),
             body: await res.text() };
  };
}

const form = (obj) => ({ method: 'POST', body: new URLSearchParams(obj).toString(),
  headers: { 'content-type': 'application/x-www-form-urlencoded' } });

// --- the token endpoint ---------------------------------------------------
async function token(params, client) {
  const headers = { 'content-type': 'application/x-www-form-urlencoded' };
  const body = new URLSearchParams(params);
  if (client.secret) {
    // A confidential client authenticates. RFC 6749 2.3.1 wants the id and
    // secret form-urlencoded before base64.
    headers.authorization = 'Basic ' + Buffer.from(
      encodeURIComponent(client.id) + ':' + encodeURIComponent(client.secret)
    ).toString('base64');
  } else {
    body.set('client_id', client.id);   // public: identified, not authenticated
  }
  const res = await fetch(AS + '/token', { method: 'POST', headers,
    body: body.toString() });
  return { status: res.status, body: await res.json() };
}

async function callApi(path, accessToken, method = 'GET') {
  const headers = {};
  if (accessToken) headers.authorization = 'Bearer ' + accessToken;
  const res = await fetch(API + path, { method, headers });
  return { status: res.status, body: await res.json() };
}

const setPolicy = (key, value) =>
  fetch(AS + '/policy', form({ key, value: String(value) })).then((r) => r.json());

// POST a form to any AS endpoint, authenticating as the given client.
async function post(path, params, client) {
  const headers = { 'content-type': 'application/x-www-form-urlencoded' };
  const body = new URLSearchParams(params);
  if (client && client.secret) {
    headers.authorization = 'Basic ' + Buffer.from(
      encodeURIComponent(client.id) + ':' + encodeURIComponent(client.secret)
    ).toString('base64');
  } else if (client) { body.set('client_id', client.id); }
  const res = await fetch(AS + path, { method: 'POST', headers,
    body: body.toString() });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

// ===========================================================================
// The front channel, walked end to end. Returns the authorization code.
// ===========================================================================
async function getCode({ client = CLIENT, scope = 'openid profile accounts:read',
                         user = 'alice', password = 'wonderland',
                         pkce = true, redirect = REDIRECT, quiet = false } = {}) {
  const browser = makeBrowser();
  const verifier = C.createVerifier();
  const challenge = C.challengeS256(verifier);
  const state = C.randomToken(16);
  const nonce = C.randomToken(12);

  const q = new URLSearchParams({ response_type: 'code', client_id: client.id,
    redirect_uri: redirect, scope, state, nonce });
  if (pkce) { q.set('code_challenge', challenge);
              q.set('code_challenge_method', 'S256'); }

  if (!quiet) {
    val('code_verifier', verifier);
    why('43 random chars. Stays here; never crosses the browser.');
    val('code_challenge', pkce ? challenge : '(none - PKCE skipped)');
    if (pkce) why('= BASE64URL(SHA256(verifier)). Safe to put in a URL.');
  }

  // The browser follows the redirect to /authorize and lands on a login form.
  let r = await browser(AS + '/authorize?' + q.toString());
  if (r.status === 302 && r.location && r.location.includes('error=')) {
    const u = new URL(r.location);
    return { error: u.searchParams.get('error'),
             description: u.searchParams.get('error_description'), verifier };
  }
  const rid = (r.body.match(/name=request_id value="([^"]+)"/) || [])[1];
  if (!rid) return { error: 'no_login_form', description: r.body.slice(0, 200) };

  // The password is typed into the IdP's own origin, not into this app.
  r = await browser(AS + '/login', form({ request_id: rid, username: user, password }));
  if (r.status === 302) r = await browser(AS + r.location.replace(AS, ''));
  const offered = [...r.body.matchAll(/name=scope value="([^"]+)"/g)].map((m) => m[1]);

  // Approve everything the consent screen actually offered.
  const consent = new URLSearchParams({ request_id: rid, action: 'allow' });
  for (const s of offered) consent.append('scope', s);
  r = await browser(AS + '/consent', { method: 'POST', body: consent.toString(),
    headers: { 'content-type': 'application/x-www-form-urlencoded' } });

  const u = new URL(r.location);
  if (u.searchParams.get('error'))
    return { error: u.searchParams.get('error'),
             description: u.searchParams.get('error_description'), verifier };
  return { code: u.searchParams.get('code'), state: u.searchParams.get('state'),
           iss: u.searchParams.get('iss'), verifier, challenge, sentState: state,
           sentNonce: nonce, offered };
}

// ===========================================================================
// The labs
// ===========================================================================
const LABS = {};

// ---------------------------------------------------------------- Lab 1
LABS.happy = { title: 'The correct flow, end to end', run: async () => {
  step(1, 'Create the PKCE pair, then walk the front channel');
  const f = await getCode();
  if (f.error) return fail(f.error + ': ' + f.description);
  val('code', f.code);
  val('state echoed', f.state);
  val('iss (RFC 9207)', f.iss);
  f.state === f.sentState ? pass('state matches - this callback is ours')
                          : fail('state mismatch');

  step(2, 'Back channel: POST /token with the code AND the verifier');
  why('The server re-hashes our verifier and compares it to the challenge it');
  why('stored in step 1. Only the app that made the verifier can pass.');
  const t = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: REDIRECT, code_verifier: f.verifier }, CLIENT);
  if (t.status !== 200) return fail(JSON.stringify(t.body));
  pass('tokens issued');
  val('token_type', t.body.token_type);
  val('expires_in', t.body.expires_in + 's');
  val('scope', t.body.scope);

  step(3, 'What is actually inside the access token');
  console.log('  ' + JSON.stringify(C.decodeJwt(t.body.access_token).header));
  console.log(JSON.stringify(C.decodeJwt(t.body.access_token).payload, null, 2)
    .split('\n').map((l) => '  ' + l).join('\n'));
  why('Note aud (which API may accept it), scope, and exp.');

  step(4, 'Call the API');
  const a = await callApi('/api/accounts', t.body.access_token);
  a.status === 200 ? pass('200 - ' + a.body.accounts.length + ' accounts')
                   : fail(JSON.stringify(a.body));

  step(5, 'Ask for something the token is not scoped for');
  const p = await callApi('/api/payments', t.body.access_token, 'POST');
  p.status === 403 ? pass('403 insufficient_scope - the API enforced scope, not '
                          + 'just a valid signature')
                   : fail('expected 403, got ' + p.status);
  why(p.body.error_description || '');
  return t.body;
} };

// ---------------------------------------------------------------- Lab 2
LABS.pkce = { title: 'The PKCE transform, step by step', run: async () => {
  const v = C.createVerifier();
  const e = C.explainPkce(v);
  step(1, 'One random string, one hash');
  val('code_verifier', e.code_verifier);
  val('length', e.length + ' characters');
  step(2, 'SHA-256 of the verifier TEXT');
  val('sha256 (hex)', e.sha256_hex);
  val('sha256 (base64)', e.sha256_base64);
  val('code_challenge', e.code_challenge);
  why('base64url is base64 with + -> - , / -> _ , and the = padding removed.');
  step(3, 'What the server does with them');
  pass('correct verifier  -> ' + C.verifyPkce(v, e.code_challenge, 'S256').ok);
  const wrong = C.verifyPkce(C.createVerifier(), e.code_challenge, 'S256');
  pass('a different one   -> ' + wrong.ok + '  (' + wrong.reason + ')');
  why('The challenge is a hash, so publishing it gives an attacker nothing:');
  why('reversing it means computing a SHA-256 preimage.');
} };

// ---------------------------------------------------------------- Lab 3
LABS.attack = { title: 'A stolen authorization code, refused by PKCE',
  run: async () => {
  step(1, 'A perfectly normal flow - and the code leaks');
  const f = await getCode({ client: SPA, scope: 'openid accounts:read',
    redirect: 'http://localhost:7020/spa-callback' });
  if (f.error) return fail(f.error + ': ' + f.description);
  val('stolen code', f.code);
  why('Assume the worst: a malicious app, a leaked log, a crafted redirect.');
  why('This is the PUBLIC client, so there is no secret to stop the attacker.');

  step(2, 'The attacker redeems it - but has no code_verifier');
  const a1 = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: 'http://localhost:7020/spa-callback' }, SPA);
  a1.status !== 200 ? pass('REFUSED: ' + a1.body.error)
                    : fail('the attack SUCCEEDED - is require_pkce off?');
  why(a1.body.error_description || '');

  step(3, 'The attacker guesses a verifier');
  const a2 = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: 'http://localhost:7020/spa-callback',
    code_verifier: C.createVerifier() }, SPA);
  a2.status !== 200 ? pass('REFUSED: the hash of a guess never matches')
                    : fail('unexpected success');
  note('The verifier is 256 bits of randomness. Guessing it is not an attack,');
  note('it is a lottery with no winning ticket. THAT is what PKCE buys you.');
} };

// ---------------------------------------------------------------- Lab 4
LABS.nopkce = { title: 'The same theft with PKCE turned OFF', run: async () => {
  note('Turning require_pkce off. This is the pre-2013 world.');
  await setPolicy('require_pkce', false);
  step(1, 'Get a code with no code_challenge at all');
  const f = await getCode({ client: SPA, scope: 'openid accounts:read',
    redirect: 'http://localhost:7020/spa-callback', pkce: false });
  if (f.error) { await setPolicy('require_pkce', true);
                 return fail(f.error + ': ' + f.description); }
  val('stolen code', f.code);

  step(2, 'The attacker redeems it with nothing but the code');
  const a = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: 'http://localhost:7020/spa-callback' }, SPA);
  if (a.status === 200) {
    fail('ATTACK SUCCEEDED - the attacker now holds a real access token');
    val('access_token', a.body.access_token.slice(0, 40) + '...');
    const d = await callApi('/api/accounts', a.body.access_token);
    if (d.status === 200) fail('and it WORKS: ' + JSON.stringify(d.body.accounts[0]));
    note('The client_id is public, so the code alone was enough. This is the');
    note('exact vulnerability OAuth 2.1 closes by making PKCE mandatory.');
  } else { pass('refused: ' + a.body.error); }

  step(3, 'Restore the safe default');
  await setPolicy('require_pkce', true);
  pass('require_pkce is back on');
} };

// ---------------------------------------------------------------- Lab 5
LABS.replay = { title: 'Replaying an already-used code', run: async () => {
  const f = await getCode({ quiet: true });
  step(1, 'First redemption (legitimate)');
  const a = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: REDIRECT, code_verifier: f.verifier }, CLIENT);
  a.status === 200 ? pass('tokens issued') : fail(JSON.stringify(a.body));
  step(2, 'Second redemption of the same code, same verifier');
  const b = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: REDIRECT, code_verifier: f.verifier }, CLIENT);
  b.status !== 200 ? pass('REFUSED: codes are single-use')
                   : fail('replay accepted');
  why(b.body.error_description || '');
} };

// ---------------------------------------------------------------- Lab 6
LABS.refresh = { title: 'Refresh rotation and reuse detection', run: async () => {
  const f = await getCode({ quiet: true });
  const first = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: REDIRECT, code_verifier: f.verifier }, CLIENT);
  const rt1 = first.body.refresh_token;
  val('refresh gen 1', rt1.slice(0, 24) + '...');

  step(1, 'Refresh once');
  const second = await token({ grant_type: 'refresh_token', refresh_token: rt1 },
    CLIENT);
  const rt2 = second.body.refresh_token;
  rt2 && rt2 !== rt1 ? pass('ROTATED - a brand-new refresh token came back')
                     : fail('no rotation');
  val('refresh gen 2', String(rt2).slice(0, 24) + '...');
  why('The old value is now retired. Each refresh token is single-use.');

  step(2, 'Replay the RETIRED generation 1, as a thief would');
  const replay = await token({ grant_type: 'refresh_token', refresh_token: rt1 },
    CLIENT);
  replay.status !== 200 ? pass('REFUSED and the whole family revoked')
                        : fail('reuse accepted');
  why(replay.body.error_description || '');

  step(3, 'The legitimate generation 2 is collateral damage');
  const after = await token({ grant_type: 'refresh_token', refresh_token: rt2 },
    CLIENT);
  after.status !== 200 ? pass('also refused - the server cannot tell thief from '
                              + 'victim, so it trusts neither')
                       : fail('gen 2 still works');
  note('The user signs in again. That is the correct, safe outcome: a silent');
  note('permanent compromise became a visible, recoverable one.');
} };

// ---------------------------------------------------------------- Lab 7
LABS.scope = { title: 'Scope is capped three times over', run: async () => {
  step(1, 'Sign in as bob and ask for payments:write');
  why('bob is not entitled to payments:write, so consent cannot offer it.');
  const f = await getCode({ user: 'bob', password: 'builder',
    scope: 'openid profile accounts:read payments:write', quiet: true });
  if (f.error) return fail(f.error + ': ' + f.description);
  val('requested', 'openid profile accounts:read payments:write');
  val('offered at consent', f.offered.join(' '));
  const t = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: REDIRECT, code_verifier: f.verifier }, CLIENT);
  val('actually granted', t.body.scope);
  !t.body.scope.includes('payments:write')
    ? pass('payments:write was dropped - the user never had it to give')
    : fail('scope escalation!');

  step(2, 'Use what bob DOES have');
  const a = await callApi('/api/accounts', t.body.access_token);
  a.status === 200 ? pass('200 on /api/accounts') : fail(JSON.stringify(a.body));

  step(3, 'Now try to move money');
  const p = await callApi('/api/payments', t.body.access_token, 'POST');
  p.status === 403 ? pass('403 insufficient_scope') : fail('expected 403');
  why('A valid token without the scope is 403, never 401. Refreshing will not');
  why('help - the user has to grant more.');
} };

// ---------------------------------------------------------------- Lab 8
LABS.idtoken = { title: 'An ID token is not an access token', run: async () => {
  const f = await getCode({ quiet: true });
  const t = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: REDIRECT, code_verifier: f.verifier }, CLIENT);

  step(1, 'Compare the two tokens this one flow returned');
  const at = C.decodeJwt(t.body.access_token), it = C.decodeJwt(t.body.id_token);
  val('access typ / aud', at.header.typ + '  /  ' + at.payload.aud);
  val('id typ / aud', it.header.typ + '  /  ' + it.payload.aud);
  why('The access token names the API. The ID token names the CLIENT.');

  step(2, 'Send the ID token to the API as a Bearer token');
  const bad = await callApi('/api/accounts', t.body.id_token);
  bad.status === 401 ? pass('401 - refused, as it must be') : fail('ACCEPTED!');
  val('check_failed', bad.body.check_failed);
  why(bad.body.error_description || '');

  step(3, 'Now a token minted for a DIFFERENT API (RFC 8707 `resource`)');
  const m2m = await token({ grant_type: 'client_credentials',
    scope: 'accounts:read', resource: 'http://localhost:9999' }, SERVICE);
  const foreign = C.decodeJwt(m2m.body.access_token);
  val('its aud', foreign.payload.aud);
  why('Genuine signature, not expired, real issuer. The only thing wrong is aud.');
  const rej = await callApi('/api/accounts', m2m.body.access_token);
  rej.status === 401 ? pass('401 ' + rej.body.check_failed + ' - refused')
                     : fail('accepted a token for another API!');
  why(rej.body.error_description || '');
  note('Without the aud check, any API trusting this issuer would accept any');
  note('token it ever minted. The audience is what confines a token to one API.');
} };

// ---------------------------------------------------------------- Lab 9
LABS.m2m = { title: 'Machine to machine: no user, no consent', run: async () => {
  step(1, 'client_credentials');
  const t = await token({ grant_type: 'client_credentials',
    scope: 'accounts:read profile' }, SERVICE);
  t.status === 200 ? pass('token issued with no browser and no consent screen')
                   : fail(JSON.stringify(t.body));
  const d = C.decodeJwt(t.body.access_token);
  val('sub', d.payload.sub);
  val('refresh_token', t.body.refresh_token ? 'present' : 'none (correct)');
  why('sub is the client_id: there is no human here. No refresh token either,');
  why('because the client can simply ask again with its own credentials.');

  step(2, 'Ask the API who the user is');
  const me = await callApi('/api/me', t.body.access_token);
  me.status === 404 ? pass('404 no_such_subject - correct, nobody delegated')
                    : note('status ' + me.status);
  note('Never use this grant to act on a user\'s behalf. Nobody consented.');
} };

// ---------------------------------------------------------------- Lab 10
LABS.revoke = { title: 'Revocation: local validation vs introspection',
  run: async () => {
  const f = await getCode({ scope: 'openid accounts:read', quiet: true });
  const t = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: REDIRECT, code_verifier: f.verifier }, CLIENT);
  const at = t.body.access_token;

  step(1, 'Both endpoints work while the token is live');
  val('local validation', (await callApi('/api/accounts', at)).status);
  val('introspection', (await callApi('/api/accounts-introspected', at)).status);

  step(2, 'Revoke the access token (RFC 7009)');
  const r = await fetch(AS + '/revoke', { method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded',
      authorization: 'Basic ' + Buffer.from(CLIENT.id + ':' + CLIENT.secret)
        .toString('base64') },
    body: new URLSearchParams({ token: at }).toString() });
  pass('revoked: ' + JSON.stringify(await r.json()));

  step(3, 'Call both endpoints again');
  const local = await callApi('/api/accounts', at);
  const intro = await callApi('/api/accounts-introspected', at);
  val('local validation', local.status + (local.status === 200
    ? '  <- STILL ACCEPTED' : ''));
  val('introspection', intro.status + (intro.status === 401
    ? '  <- correctly refused' : ''));
  note('A self-contained JWT knows nothing about being revoked: its signature');
  note('and exp are unchanged. Introspection asks the server and sees the');
  note('truth, at the cost of a round trip per call. THAT is why access tokens');
  note('are given lifetimes measured in minutes.');
} };

// ---------------------------------------------------------------- Lab 11
LABS.oidc = { title: 'Who is this? (OpenID Connect)', run: async () => {
  step(1, 'Ask for the openid scope, and send a nonce');
  why('That one scope is what turns an OAuth grant into an OpenID Connect one.');
  const f = await getCode({ scope: 'openid profile email accounts:read' });
  if (f.error) return fail(f.error + ': ' + f.description);
  val('nonce we sent', f.sentNonce);
  const t = await token({ grant_type: 'authorization_code', code: f.code,
    redirect_uri: REDIRECT, code_verifier: f.verifier }, CLIENT);
  if (t.status !== 200) return fail(JSON.stringify(t.body));
  t.body.id_token ? pass('the response now carries an id_token as well')
                  : fail('no id_token - was the openid scope granted?');

  step(2, 'Two tokens, two different jobs');
  const at = C.decodeJwt(t.body.access_token), it = C.decodeJwt(t.body.id_token);
  val('access  typ / aud', at.header.typ + '  /  ' + at.payload.aud);
  val('id      typ / aud', it.header.typ + '  /  ' + it.payload.aud);
  why('The access token is addressed to the API. The ID token is addressed to');
  why('THIS CLIENT - which is why the client is the one that validates it.');
  console.log(JSON.stringify(it.payload, null, 2).split('\n')
    .map((l) => '  ' + l).join('\n'));

  step(3, 'Validate the ID token the way a client must');
  const jwks = await (await fetch(AS + '/jwks.json')).json();
  const crypto = require('node:crypto');
  const key = crypto.createPublicKey({ key: jwks.keys[0], format: 'jwk' });
  // signature + iss + aud + exp, all in one pass
  const v = C.verifyJwt(t.body.id_token, { getKey: () => key, issuer: AS,
    audience: CLIENT.id, requiredTyp: 'JWT' });
  v.valid ? pass('signature, iss, aud and exp all check out')
          : fail('rejected: ' + v.reason);
  // the nonce is the check people forget: it ties this token to THIS request
  it.payload.nonce === f.sentNonce
    ? pass('nonce matches the one we generated - not a replayed token')
    : fail('nonce mismatch');
  val('sub', it.payload.sub + '   <- the stable identifier. Key your user on this.');
  val('auth_time', it.payload.auth_time + '   (when they actually authenticated)');

  step(4, 'A token minted for someone else must be refused');
  const other = C.verifyJwt(t.body.id_token, { getKey: () => key, issuer: AS,
    audience: 'some-other-app', requiredTyp: 'JWT' });
  !other.valid ? pass('REFUSED: ' + other.reason + ' - aud is checked, not assumed')
               : fail('accepted a token addressed to another client!');
  why('Without this check, any client of this issuer could replay an ID token');
  why('at any other client and be logged in as that user.');

  step(5, 'Fetch fresh claims from /userinfo');
  why('Note which token it takes: the ACCESS token, not the ID token.');
  let r = await fetch(AS + '/userinfo',
    { headers: { authorization: 'Bearer ' + t.body.access_token } });
  let b = await r.json();
  r.status === 200 ? pass('200 ' + JSON.stringify(b)) : fail(JSON.stringify(b));
  why('The claims are filtered by granted scope, exactly as in the ID token.');

  step(6, 'And without the openid scope there is no identity to return');
  const f2 = await getCode({ scope: 'accounts:read', quiet: true });
  const t2 = await token({ grant_type: 'authorization_code', code: f2.code,
    redirect_uri: REDIRECT, code_verifier: f2.verifier }, CLIENT);
  val('id_token present', t2.body.id_token ? 'yes' : 'no  <- correct');
  r = await fetch(AS + '/userinfo',
    { headers: { authorization: 'Bearer ' + t2.body.access_token } });
  b = await r.json();
  r.status === 403 ? pass('403 ' + b.error + ' at /userinfo')
                   : fail('expected 403, got ' + r.status);
  note('OAuth answers "what may this app do". OpenID Connect answers "who is');
  note('this, and when did they prove it". You opt into the second with a scope.');
} };

// ---------------------------------------------------------------- Lab 12
LABS['agent-device'] = { title: 'An agent with no browser (device grant)',
  run: async () => {
  step(1, 'The agent asks for a code a human can type somewhere else');
  why('It has no browser, so there is no redirect it could ever receive.');
  const limits = [{ type: 'payment_initiation', actions: ['initiate'],
                    maxAmount: '100.00', fromAccount: 'ACC-4410' }];
  const start = await post('/device_authorization', {
    scope: 'openid accounts:read payments:write',
    authorization_details: JSON.stringify(limits) }, AGENT);
  if (start.status !== 200) return fail(JSON.stringify(start.body));
  val('user_code', start.body.user_code);
  val('verification_uri', start.body.verification_uri);
  val('poll interval', start.body.interval + 's');
  why('The agent shows that code to the operator and starts polling.');

  step(2, 'The agent polls before anyone has approved');
  let t = await post('/token', { grant_type: DEVICE_GRANT,
    device_code: start.body.device_code }, AGENT);
  t.body.error === 'authorization_pending'
    ? pass('authorization_pending - correct, it must keep waiting')
    : fail(JSON.stringify(t.body));

  step(3, 'Polling too fast is told to back off');
  t = await post('/token', { grant_type: DEVICE_GRANT,
    device_code: start.body.device_code }, AGENT);
  t.body.error === 'slow_down' ? pass('slow_down - the interval is enforced')
                               : note('got ' + t.body.error);

  step(4, 'The human approves on their own device');
  const approve = await fetch(AS + '/device', form({
    user_code: start.body.user_code, username: 'alice',
    password: 'wonderland', action: 'allow' }));
  (await approve.text()).includes('Approved')
    ? pass('approved by alice, at the IdP, on a device with a screen')
    : fail('approval failed');

  step(5, 'The next poll returns tokens');
  await pause(5200);   // respect the advertised interval
  t = await post('/token', { grant_type: DEVICE_GRANT,
    device_code: start.body.device_code }, AGENT);
  if (t.status !== 200) return fail(JSON.stringify(t.body));
  pass('tokens issued to the agent');
  val('scope', t.body.scope);
  val('refresh_token', t.body.refresh_token ? 'yes - it can work unattended' : 'none');
  const claims = C.decodeJwt(t.body.access_token).payload;
  val('sub', claims.sub + '   (the USER, not the agent)');
  val('client_id', claims.client_id + '   (the agent)');
  console.log('  authorization_details:',
    JSON.stringify(claims.authorization_details));
  note('The token acts for alice but records which agent holds it. That pair');
  note('is what makes agent activity auditable.');
  return t.body;
} };

// ---------------------------------------------------------------- Lab 13
LABS['agent-rar'] = { title: 'Structured limits a scope cannot express',
  run: async () => {
  const tok = await LABS['agent-device'].run();
  if (!tok || !tok.access_token) return fail('could not get an agent token');
  const at = tok.access_token;

  step(6, 'A payment inside the granted limit');
  let r = await fetch(API + '/api/payments', { method: 'POST',
    headers: { authorization: 'Bearer ' + at,
      'content-type': 'application/x-www-form-urlencoded' },
    body: 'amount=50&from=ACC-4410' });
  let b = await r.json();
  r.status === 201 ? pass('201 - payment of 50 accepted')
                   : fail(r.status + ' ' + JSON.stringify(b));

  step(7, 'The same token, a much larger payment');
  r = await fetch(API + '/api/payments', { method: 'POST',
    headers: { authorization: 'Bearer ' + at,
      'content-type': 'application/x-www-form-urlencoded' },
    body: 'amount=5000&from=ACC-4410' });
  b = await r.json();
  r.status === 403 ? pass('403 ' + b.error) : fail('expected 403, got ' + r.status);
  why(b.error_description || '');

  step(8, 'And from an account it was never granted');
  r = await fetch(API + '/api/payments', { method: 'POST',
    headers: { authorization: 'Bearer ' + at,
      'content-type': 'application/x-www-form-urlencoded' },
    body: 'amount=10&from=ACC-9982' });
  b = await r.json();
  r.status === 403 ? pass('403 ' + b.error) : fail('expected 403, got ' + r.status);
  note('scope=payments:write was satisfied every time. The structured grant is');
  note('what stopped it. This is why agents need RFC 9396, not bigger scopes.');
} };

// ---------------------------------------------------------------- Lab 14
LABS['agent-exchange'] = { title: 'Delegation chains and the act claim',
  run: async () => {
  step(1, 'An agent obtains a user token (device grant, as in Lab 11)');
  const tok = await (async () => {
    const s2 = await post('/device_authorization',
      { scope: 'openid accounts:read' }, AGENT);
    await fetch(AS + '/device', form({ user_code: s2.body.user_code,
      username: 'alice', password: 'wonderland', action: 'allow' }));
    await pause(5200);
    return (await post('/token',
      { grant_type: DEVICE_GRANT, device_code: s2.body.device_code }, AGENT)).body;
  })();
  if (!tok.access_token) return fail('no agent token');
  val('agent token sub', C.decodeJwt(tok.access_token).payload.sub);

  step(2, 'The agent hands it to a backend service, which must call the API');
  why('That service must not pretend to BE alice, and must not act as itself');
  why('either. It exchanges the token for one that records both.');
  const ex = await post('/token', { grant_type: EXCHANGE_GRANT,
    subject_token: tok.access_token,
    subject_token_type: 'urn:ietf:params:oauth:token-type:access_token',
    scope: 'accounts:read' }, ORCH);
  if (ex.status !== 200) return fail(JSON.stringify(ex.body));
  pass('exchanged');
  const c = C.decodeJwt(ex.body.access_token).payload;
  val('sub', c.sub + '   (still alice - the authority is hers)');
  console.log('  act:', JSON.stringify(c.act) + '   <- who is acting');

  step(3, 'The API sees the whole chain');
  const r = await fetch(API + '/api/accounts',
    { headers: { authorization: 'Bearer ' + ex.body.access_token } });
  const b = await r.json();
  val('status', r.status);
  val('acting_chain', JSON.stringify(b.acting_chain));
  note('Without `act`, that request would look exactly like alice at a browser.');
  note('With it, an auditor can answer "which agent did this?" months later.');
} };

// ---------------------------------------------------------------- Lab 15
LABS['agent-deputy'] = { title: 'A confused deputy tries to escalate',
  run: async () => {
  step(1, 'An agent is delegated read access only');
  const s2 = await post('/device_authorization',
    { scope: 'openid accounts:read' }, AGENT);
  await fetch(AS + '/device', form({ user_code: s2.body.user_code,
    username: 'alice', password: 'wonderland', action: 'allow' }));
  await pause(5200);
  const tok = (await post('/token',
    { grant_type: DEVICE_GRANT, device_code: s2.body.device_code }, AGENT)).body;
  val('granted scope', tok.scope);

  step(2, 'The backend tries to exchange it for permission to move money');
  why('This is the confused deputy: a trusted service being talked into using');
  why('authority it holds on behalf of someone who never granted it.');
  const ex = await post('/token', { grant_type: EXCHANGE_GRANT,
    subject_token: tok.access_token,
    subject_token_type: 'urn:ietf:params:oauth:token-type:access_token',
    scope: 'accounts:read payments:write' }, ORCH);
  ex.status !== 200 ? pass('REFUSED: ' + ex.body.error)
                    : fail('escalation succeeded - delegation must only narrow');
  why(ex.body.error_description || '');

  step(3, 'Even the agent\'s own token cannot reach the payments endpoint');
  const r = await fetch(API + '/api/payments', { method: 'POST',
    headers: { authorization: 'Bearer ' + tok.access_token,
      'content-type': 'application/x-www-form-urlencoded' },
    body: 'amount=10' });
  r.status === 403 ? pass('403 insufficient_scope at the API too')
                   : fail('expected 403, got ' + r.status);
  note('Two independent checks refused it: the AS would not widen the grant,');
  note('and the API would not honour a scope the token never carried.');
} };

// ===========================================================================
const ORDER = ['happy', 'pkce', 'attack', 'nopkce', 'replay', 'refresh',
               'scope', 'idtoken', 'm2m', 'revoke',
               'oidc', 'agent-device', 'agent-rar', 'agent-exchange',
               'agent-deputy'];

async function main() {
  const which = process.argv[2] || 'list';

  if (which === 'list') {
    console.log('\n' + B + '  OAuth 2.1 + PKCE labs' + R + '\n');
    ORDER.forEach((k, i) => console.log('  ' + String(i + 1).padStart(2)
      + '  ' + k.padEnd(15) + LABS[k].title));
    console.log('\n  node client.js <name>   |   node client.js all\n');
    return;
  }

  // Both servers must be up before any lab can run.
  for (const [name, url] of [['authorization server', AS], ['api', API]]) {
    try { await fetch(url + '/healthz'); }
    catch { console.log(RE + '\n  Cannot reach the ' + name + ' at ' + url
      + '.\n  Start it first:  node ' + (url === AS ? 'authz-server'
      : 'api-server') + '.js\n' + R); process.exit(1); }
  }

  const names = which === 'all' ? ORDER : [which];
  for (const n of names) {
    if (!LABS[n]) { console.log('Unknown lab: ' + n); process.exit(2); }
    head('LAB ' + (ORDER.indexOf(n) + 1) + '  ' + LABS[n].title);
    await LABS[n].run();
  }
  console.log('\n' + D + '  Watch the server side of all of this in the terminal'
    + ' running authz-server.js\n' + R);
}

main().catch((e) => { console.error(e); process.exit(1); });
