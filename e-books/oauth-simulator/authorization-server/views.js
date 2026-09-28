'use strict';
/** Browser-facing pages of the authorization server / IdP. */
const { page, card, pre, preJson, note, pill, kvTable, escapeHtml } = require('../shared/ui');
const { SCOPES } = require('./config');

const NAV = [
  { label: 'Overview', href: '/' },
  { label: 'Discovery', href: '/.well-known/oauth-authorization-server' },
  { label: 'Keys', href: '/jwks.json' },
  { label: 'Policy', href: '/policy' },
  { label: 'State', href: '/state' },
  { label: 'Trace', href: '/trace' },
];

const BRAND = 'Simulator IdP + Authorization Server';
const FOOT = 'This authorization server is a teaching simulator. Keys are generated at boot and all state is in memory &mdash; never use it for anything real.';

/** The IdP login form. This is authentication -- it is not OAuth. */
function loginPage({ requestId, client, scopes, error, prefill = '' }) {
  const scopeList = scopes.map((s) => `<li><b class="mono">${escapeHtml(s)}</b> &mdash; ${escapeHtml((SCOPES[s] || {}).label || 'unknown scope')}</li>`).join('');
  const body = `
${card(`
  <h2 style="margin-bottom:2px">Sign in to continue</h2>
  <p class="muted small">
    <b>${escapeHtml(client.client_name)}</b> wants to act on your behalf.
    Notice that you are typing your password into <b>the identity provider</b>
    (<span class="mono">localhost:9000</span>), never into the application.
    That is the entire point of OAuth.
  </p>
  ${error ? note(`<b>${escapeHtml(error)}</b>`, 'bad') : ''}
  <form method="POST" action="/login">
    <input type="hidden" name="request_id" value="${escapeHtml(requestId)}">
    <label class="field">Username
      <input type="text" name="username" autocomplete="username" autofocus value="${escapeHtml(prefill)}" placeholder="alice">
    </label>
    <label class="field">Password
      <input type="password" name="password" autocomplete="current-password" placeholder="wonderland">
    </label>
    <div class="row" style="margin-top:16px">
      <button class="btn" type="submit">Sign in</button>
      <button class="btn ghost" type="submit" name="action" value="deny">Cancel</button>
    </div>
  </form>
`)}
${card(`
  <h3>Test accounts</h3>
  <table><thead><tr><th>Username</th><th>Password</th><th>Can delegate</th></tr></thead><tbody>
    <tr><td class="v">alice</td><td class="v">wonderland</td><td class="tiny">everything, including <span class="mono">payments:write</span></td></tr>
    <tr><td class="v">bob</td><td class="v">builder</td><td class="tiny">read-only &mdash; no <span class="mono">payments:write</span></td></tr>
  </tbody></table>
`, 'tight')}
${card(`<h3>This app is requesting</h3><ul class="small" style="margin:6px 0 0;padding-left:20px">${scopeList}</ul>`, 'tight')}`;
  return page({ title: 'Sign in', body, nav: [], brand: BRAND, footer: FOOT, wrapClass: 'narrow' });
}

/** The consent (authorization) screen. This *is* OAuth: delegation. */
function consentPage({ requestId, client, user, requestedScopes, grantableScopes, refusedScopes, alreadyGranted }) {
  const rows = grantableScopes.map((s) => {
    const meta = SCOPES[s] || { label: s, description: '' };
    const required = s === 'openid';
    return `<label style="display:flex;gap:11px;align-items:flex-start;padding:11px 0;border-bottom:1px solid var(--line)">
      <input type="checkbox" name="scope" value="${escapeHtml(s)}" checked ${required ? 'disabled' : ''} style="margin-top:4px;width:16px;height:16px">
      <span>
        <b>${escapeHtml(meta.label)}</b> <span class="pill mute">${escapeHtml(s)}</span>
        ${required ? pill('required', 'warn') : ''}
        <br><span class="muted small">${escapeHtml(meta.description)}</span>
      </span>
    </label>`;
  }).join('');

  const refused = refusedScopes.length
    ? note(`<b>${escapeHtml(user.username)} cannot delegate</b> <span class="mono">${refusedScopes.map(escapeHtml).join(' ')}</span>.
       The app asked for it, but the user does not hold it, so it is dropped from the grant.
       <span class="muted">A client never receives more authority than the user has.</span>`, 'warn')
    : '';

  const body = `
${card(`
  <h2 style="margin-bottom:2px">Allow access?</h2>
  <p class="muted small">Signed in as <b>${escapeHtml(user.name)}</b> (<span class="mono">${escapeHtml(user.username)}</span>)
    &middot; <a href="/logout?return=${encodeURIComponent(`/authorize/resume?request_id=${requestId}`)}">switch user</a></p>
  <p><b>${escapeHtml(client.client_name)}</b> <span class="pill ${client.client_type === 'public' ? 'warn' : 'ok'}">${escapeHtml(client.client_type)}</span>
     is asking for permission to:</p>
  ${refused}
  <form method="POST" action="/consent">
    <input type="hidden" name="request_id" value="${escapeHtml(requestId)}">
    <div style="margin:8px 0 18px">${rows || '<p class="muted">No grantable scopes.</p>'}</div>
    <div class="row">
      <button class="btn" type="submit" name="action" value="allow">Allow</button>
      <button class="btn ghost" type="submit" name="action" value="deny">Deny</button>
      <span class="muted tiny">Deny sends <span class="mono">error=access_denied</span> back to the app &mdash; a normal, expected outcome apps must handle.</span>
    </div>
  </form>
`)}
${alreadyGranted && alreadyGranted.length ? card(`<h3>Previously granted</h3><p class="small mono">${alreadyGranted.map(escapeHtml).join(' ')}</p>
  <p class="muted tiny">Because these were approved before, a real AS would skip this screen entirely. The simulator always shows it so you can see it.</p>`, 'tight') : ''}
${card(`<h3>Raw authorization request</h3>${kvTable({
  client_id: client.client_id,
  scope: requestedScopes.join(' '),
  request_id: requestId,
})}`, 'tight')}`;
  return page({ title: 'Consent', body, nav: [], brand: BRAND, footer: FOOT, wrapClass: 'narrow' });
}

/**
 * Errors the AS must NOT redirect back to the client.
 *
 * If the client_id is unknown or the redirect_uri does not match exactly, the
 * AS has no trustworthy place to send the user, so it renders the error here
 * instead. Redirecting to an unvalidated URI is an open redirector.
 */
function authorizeErrorPage({ error, description, hint, details }) {
  const body = `
${card(`
  <h2 style="margin-bottom:4px"><span class="pill err">${escapeHtml(error)}</span></h2>
  <p style="font-size:16px">${escapeHtml(description)}</p>
  ${hint ? note(hint, 'warn') : ''}
  ${details ? `<h3>Request as received</h3>${kvTable(details)}` : ''}
  ${note(`<b>Why you are seeing this page instead of being redirected:</b> the authorization server could not
    validate <span class="mono">client_id</span> + <span class="mono">redirect_uri</span>. Until those two check
    out, any redirect would turn this endpoint into an open redirector that an attacker could point anywhere.
    Errors are only delivered to the client once its identity and its callback URL are proven.`)}
  <p><a class="btn ghost" href="/">Back to overview</a></p>
`)}`;
  return page({ title: `Error: ${error}`, body, nav: NAV, active: 'Overview', brand: BRAND, footer: FOOT, wrapClass: 'narrow' });
}

function homePage({ issuer, clients, policy, users }) {
  const clientRows = clients.map((c) => `<tr>
    <td class="v">${escapeHtml(c.client_id)}</td>
    <td class="tiny">${escapeHtml(c.client_name)}</td>
    <td>${pill(c.client_type, c.client_type === 'public' ? 'warn' : 'ok')}</td>
    <td class="tiny mono">${c.grant_types.map(escapeHtml).join('<br>')}</td>
    <td class="tiny mono break">${c.redirect_uris.map(escapeHtml).join('<br>') || '<span class="muted">none</span>'}</td>
    <td>${c.require_pkce ? pill('PKCE', 'ok') : pill('n/a', 'mute')}</td>
  </tr>`).join('');

  const policyRows = Object.entries(policy).map(([k, v]) => `<tr>
    <td class="k">${escapeHtml(k)}</td>
    <td>${v ? pill('on', 'ok') : pill('off', 'err')}</td></tr>`).join('');

  const body = `
${card(`
  <h2 style="margin-bottom:2px">What this service is</h2>
  <p class="small">Two things fused into one container, which is how most real products ship it:</p>
  <div class="grid two" style="margin-top:10px">
    <div>
      <h3 style="margin-top:0">Identity Provider (IdP)</h3>
      <p class="small muted">Owns the login form and the user database. Answers <b>&ldquo;who is this person?&rdquo;</b>
      Its output is a browser session and, if you ask for the <span class="mono">openid</span> scope, an ID token.</p>
    </div>
    <div>
      <h3 style="margin-top:0">Authorization Server (AS)</h3>
      <p class="small muted">Runs the OAuth 2.1 endpoints. Answers <b>&ldquo;may this app do this, on whose behalf?&rdquo;</b>
      Its output is an access token, scoped and time-limited.</p>
    </div>
  </div>
`)}
${card(`
  <h2>Endpoints</h2>
  <table><thead><tr><th>Endpoint</th><th>Method</th><th>What it does</th></tr></thead><tbody>
    <tr><td class="v"><a href="/.well-known/oauth-authorization-server">/.well-known/oauth-authorization-server</a></td><td>${pill('get', 'get')}</td><td class="tiny">RFC 8414 metadata. A client reads this instead of hard-coding URLs.</td></tr>
    <tr><td class="v"><a href="/.well-known/openid-configuration">/.well-known/openid-configuration</a></td><td>${pill('get', 'get')}</td><td class="tiny">The OpenID Connect flavour of the same document.</td></tr>
    <tr><td class="v">/authorize</td><td>${pill('get', 'get')}</td><td class="tiny"><b>Front channel.</b> Runs in the browser. Authenticates the user, collects consent, returns a code.</td></tr>
    <tr><td class="v">/token</td><td>${pill('post', 'post')}</td><td class="tiny"><b>Back channel.</b> Server-to-server. Exchanges code + <span class="mono">code_verifier</span> for tokens.</td></tr>
    <tr><td class="v">/userinfo</td><td>${pill('get', 'get')}</td><td class="tiny">OIDC claims about the signed-in user, gated by scope.</td></tr>
    <tr><td class="v">/introspect</td><td>${pill('post', 'post')}</td><td class="tiny">RFC 7662. Lets a resource server ask &ldquo;is this token still good?&rdquo;</td></tr>
    <tr><td class="v">/revoke</td><td>${pill('post', 'post')}</td><td class="tiny">RFC 7009. Throws a token away early (logout).</td></tr>
    <tr><td class="v"><a href="/jwks.json">/jwks.json</a></td><td>${pill('get', 'get')}</td><td class="tiny">Public keys, so any API can verify a token offline.</td></tr>
    <tr><td class="v"><a href="/state">/state</a></td><td>${pill('get', 'get')}</td><td class="tiny">Simulator extra: everything this AS is currently remembering.</td></tr>
    <tr><td class="v"><a href="/policy">/policy</a></td><td>${pill('get', 'get')}</td><td class="tiny">Simulator extra: turn safety rules off and watch attacks land.</td></tr>
  </tbody></table>
`)}
<div class="grid two">
${card(`<h2>Registered clients</h2><table><thead><tr><th>client_id</th><th>Name</th><th>Type</th><th>Grants</th><th>redirect_uri</th><th></th></tr></thead><tbody>${clientRows}</tbody></table>`)}
${card(`<h2>Current policy</h2><table><tbody>${policyRows}</tbody></table>
  <p class="tiny muted">All ON = a correct OAuth 2.1 server. Change these at <a href="/policy">/policy</a>.</p>`)}
</div>
${card(`<h2>Users</h2><table><thead><tr><th>Username</th><th>Password</th><th>sub</th><th>Entitlements</th></tr></thead><tbody>
  ${users.map((u) => `<tr><td class="v">${escapeHtml(u.username)}</td><td class="v">${escapeHtml(u.password)}</td><td class="v">${escapeHtml(u.sub)}</td><td class="tiny mono">${u.entitlements.map(escapeHtml).join(' ')}</td></tr>`).join('')}
</tbody></table>`)}
${card(`<h3>Issuer</h3>${pre(issuer)}
  <p class="tiny muted">Every token this server mints carries this exact string as <span class="mono">iss</span>, and the
  resource server rejects anything else. Inside Docker the containers talk to each other as
  <span class="mono">http://authorization-server:9000</span>, but the issuer stays browser-facing.</p>`, 'tight')}`;
  return page({ title: 'Authorization Server', subtitle: 'The IdP and OAuth 2.1 authorization server for this simulator. Start at the client app on <a href="http://localhost:9020">localhost:9020</a>.', body, nav: NAV, active: 'Overview', brand: BRAND, footer: FOOT });
}

function policyPage({ policy, defaults }) {
  const explain = {
    require_pkce: 'Reject any authorization_code request without a code_challenge. <b>Turn this off to run Lab 02 and watch a stolen code work.</b>',
    allow_plain_code_challenge: 'Accept code_challenge_method=plain, where the "hash" is the verifier itself. Anyone who sees the challenge can forge the verifier.',
    require_exact_redirect_uri: 'Compare redirect_uri by exact string equality. Off = prefix matching, which lets an attacker append their own path.',
    require_state: 'Require the state parameter on /authorize. With PKCE this is no longer the anti-CSRF mechanism, but it is still good practice.',
    single_use_codes: 'A code may be redeemed once. Off = a replayed code mints fresh tokens for whoever replays it.',
    rotate_refresh_tokens: 'Issue a brand-new refresh token on every refresh and retire the old one.',
    detect_refresh_reuse: 'If a retired refresh token is presented, revoke the entire family. This is how you survive a stolen refresh token.',
    enforce_audience: 'Put an aud claim on access tokens so a token minted for one API is rejected by another.',
    allow_legacy_grants: 'Re-enable the implicit and password grants that OAuth 2.1 removed. <b>Turn on to see exactly what you have been spared.</b>',
  };
  const rows = Object.entries(policy).map(([k, v]) => `<tr>
    <td style="width:1%"><form method="POST" action="/policy" style="margin:0">
      <input type="hidden" name="key" value="${escapeHtml(k)}">
      <input type="hidden" name="value" value="${v ? 'false' : 'true'}">
      <button class="btn sm ${v ? '' : 'danger'}" type="submit">${v ? 'Turn off' : 'Turn on'}</button>
    </form></td>
    <td><b class="mono">${escapeHtml(k)}</b> ${v ? pill('on', 'ok') : pill('off', 'err')}
      ${v !== defaults[k] ? pill('unsafe', 'err') : ''}
      <br><span class="muted small">${explain[k] || ''}</span></td>
  </tr>`).join('');
  const unsafe = Object.entries(policy).filter(([k, v]) => v !== defaults[k]);
  const body = `
${unsafe.length ? note(`<b>This authorization server is currently running unsafely.</b> Deviations:
  <span class="mono">${unsafe.map(([k]) => escapeHtml(k)).join(', ')}</span>.
  <form method="POST" action="/policy/reset" style="display:inline;margin-left:8px"><button class="btn sm" type="submit">Restore safe defaults</button></form>`, 'bad') : note('<b>All safety rules are on.</b> This is a correctly configured OAuth 2.1 authorization server.', 'good')}
${card(`<table><tbody>${rows}</tbody></table>`)}
${note('Changing policy here changes how the server behaves immediately &mdash; no restart. The trace log records a warning every time an unsafe path is taken, so you can always see <i>why</i> something succeeded that should not have.')}`;
  return page({ title: 'Policy switches', subtitle: 'Break the rules on purpose. Each switch below corresponds to a requirement in OAuth 2.1 or RFC 9700 (Security Best Current Practice).', body, nav: NAV, active: 'Policy', brand: BRAND, footer: FOOT });
}

function statePage(snapshot) {
  const body = `
${card(`<h2>IdP sessions</h2><p class="muted small">A logged-in browser. This is what lets a second app skip the login form &mdash; single sign-on.</p>${preJson(snapshot.sessions)}`)}
${card(`<h2>Authorization codes</h2><p class="muted small">Short-lived, single-use, and each one bound to the PKCE challenge that was presented at <span class="mono">/authorize</span>.</p>${preJson(snapshot.codes)}`)}
${card(`<h2>Refresh tokens</h2><p class="muted small">Grouped by <span class="mono">familyId</span>. Every rotation increments <span class="mono">generation</span>; presenting a retired one revokes the whole family.</p>${preJson(snapshot.refreshTokens)}`)}
${card(`<h2>Remembered consent</h2><p class="muted small">What each user has already agreed to give each app.</p>${preJson(snapshot.grants)}`)}
${card(`<h2>Pending /authorize requests</h2>${preJson(snapshot.pendingRequests)}`)}
${card(`<h3>Housekeeping</h3><form method="POST" action="/state/reset"><button class="btn danger" type="submit">Wipe all state</button>
  <span class="muted tiny" style="margin-left:8px">Clears sessions, codes, tokens and consent. Signing keys survive.</span></form>`, 'tight')}`;
  return page({ title: 'Server state', subtitle: 'Everything this authorization server is remembering right now. Refresh after each step of a flow to watch it change.', body, nav: NAV, active: 'State', brand: BRAND, footer: FOOT });
}

function tracePage() {
  const body = `
${card(`<div class="row" style="margin-bottom:8px">
  <b>Live protocol trace</b> <span class="pill ok" id="status">connecting</span>
  <button class="btn sm ghost" onclick="document.getElementById('log').innerHTML=''" style="margin-left:auto">Clear view</button>
</div>
<div class="trace" id="log"></div>`)}
${note('Every decision this server makes is emitted here as it happens: which rule was checked, what it compared, and why the request passed or failed. Keep this open in a second window while you drive a flow from the client app.')}
<script>
const log = document.getElementById('log');
const status = document.getElementById('status');
function render(e){
  const row = document.createElement('div');
  row.className = 'ev ' + (e.level === 'error' ? 'error' : e.level === 'ok' ? 'ok' : '');
  const detail = Object.keys(e.data || {}).length ? ' ' + JSON.stringify(e.data) : '';
  row.innerHTML = '<span class="t">' + e.at.slice(11,23) + '</span><span class="n">' + e.level.toUpperCase() + '</span><span class="m"></span>';
  row.querySelector('.m').textContent = e.message + detail;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}
fetch('/trace.json').then(r => r.json()).then(d => d.events.forEach(render));
const es = new EventSource('/trace/stream');
es.addEventListener('trace', (m) => render(JSON.parse(m.data)));
es.onopen = () => { status.textContent = 'live'; status.className = 'pill ok'; };
es.onerror = () => { status.textContent = 'disconnected'; status.className = 'pill err'; };
</script>`;
  return page({ title: 'Trace', subtitle: 'A live stream of protocol decisions inside the authorization server.', body, nav: NAV, active: 'Trace', brand: BRAND, footer: FOOT });
}

function legacyGrantPage({ grantType, why }) {
  const body = `${card(`
  <h2><span class="pill err">removed in OAuth 2.1</span></h2>
  <p style="font-size:16px">The <b class="mono">${escapeHtml(grantType)}</b> grant no longer exists.</p>
  ${note(why, 'bad')}
  <p><a class="btn ghost" href="/policy">See the policy switch that re-enables it</a></p>`)}`;
  return page({ title: `${grantType} removed`, body, nav: NAV, brand: BRAND, footer: FOOT, wrapClass: 'narrow' });
}

module.exports = { loginPage, consentPage, authorizeErrorPage, homePage, policyPage, statePage, tracePage, legacyGrantPage, NAV, BRAND, FOOT };
