'use strict';
/** Pages of the client application: the lab catalogue and the flow inspector. */
const { page, card, pre, preJson, note, pill, kvTable, escapeHtml } = require('../shared/ui');
const { authCodePkceDiagram } = require('../shared/diagram');
const { LABS, ACTION_LABELS } = require('./labs');

const NAV = [
  { label: 'Labs', href: '/' },
  { label: 'Flow', href: '/flow' },
  { label: 'Tokens', href: '/tokens' },
  { label: 'Concepts', href: '/concepts' },
  { label: 'AS', href: 'http://localhost:9000' },
  { label: 'API', href: 'http://localhost:9010' },
];
const BRAND = 'OAuth 2.1 Simulator &mdash; Client App';
const FOOT = 'A teaching simulator for OAuth 2.1 with PKCE. Client on :9020, authorization server + IdP on :9000, resource server on :9010. Developer guide: <span class="mono">dev-guide/dev-guide.pdf</span>.';

const LEVEL_PILL = { 'start here': 'ok', core: 'mute', attack: 'err', advanced: 'warn' };

function tokenBanner(session) {
  if (!session.tokens || !session.tokens.access_token) {
    return `<div class="row small muted">${pill('no tokens', 'mute')} Run a lab to obtain one.</div>`;
  }
  const left = session.tokens.expires_at - Math.floor(Date.now() / 1000);
  return `<div class="row small">
    ${pill('access token held', left > 0 ? 'ok' : 'err')}
    <span class="muted">scope</span> <span class="mono">${escapeHtml(session.tokens.scope || '')}</span>
    <span class="muted">expires in</span> <span class="mono">${left > 0 ? `${left}s` : 'expired'}</span>
    ${session.tokens.refresh_token ? pill('refresh token held', 'ok') : ''}
    ${session.tokens.id_token ? pill('id token held', 'mute') : ''}
    <a class="btn sm ghost" href="/tokens" style="margin-left:auto">Inspect</a>
  </div>`;
}

function homePage(session) {
  const cards = LABS.map((lab) => `
  <a class="lab-card" href="/labs/${lab.id}">
    <span class="n">LAB ${lab.num}</span> ${pill(lab.level, LEVEL_PILL[lab.level] || 'mute')}
    <h3>${escapeHtml(lab.title)}</h3>
    <p>${escapeHtml(lab.tagline)}</p>
  </a>`).join('');

  const body = `
${card(`
  <h2 style="margin-top:0">What you are looking at</h2>
  <p class="small">Three services are running. Together they are a complete, working OAuth 2.1 deployment:</p>
  <div class="grid three" style="margin:12px 0">
    <div class="card tight" style="margin:0">
      <b>Client App</b> ${pill(':9020', 'mute')}
      <p class="small muted" style="margin:6px 0 0">This page. Wants data on your behalf. Holds no password of yours, ever.</p>
    </div>
    <div class="card tight" style="margin:0">
      <b>IdP + Authorization Server</b> ${pill(':9000', 'mute')}
      <p class="small muted" style="margin:6px 0 0">Authenticates you, asks your permission, issues tokens.
      <a href="http://localhost:9000" target="_blank">Open</a></p>
    </div>
    <div class="card tight" style="margin:0">
      <b>Resource Server</b> ${pill(':9010', 'mute')}
      <p class="small muted" style="margin:6px 0 0">The API holding the data. Trusts tokens, not sessions.
      <a href="http://localhost:9010" target="_blank">Open</a></p>
    </div>
  </div>
  ${tokenBanner(session)}
`)}
${card(`<h2 style="margin-top:0">The flow you are about to run</h2>
  ${authCodePkceDiagram([], 860)}
  <p class="tiny muted">Solid arrows are direct HTTP calls; dashed arrows are browser redirects. Steps 3&ndash;6 are the
  <b>front channel</b> (visible in the URL bar). Step 7 is the <b>back channel</b> (server to server, invisible to the browser).</p>`)}
<h2>Labs</h2>
<p class="lede">Start with Lab 01. The attack labs are the interesting ones &mdash; each breaks a rule on purpose and shows you what the rule was for.</p>
<div class="grid two">${cards}</div>
${card(`<h3>Prefer a terminal?</h3>${pre('./scripts/demo-flow.sh --all')}
  <p class="tiny muted">Runs the same flows with curl, printing every parameter. No browser involved.</p>`, 'tight')}`;

  return page({
    title: 'OAuth 2.1 + PKCE Simulator',
    subtitle: 'A complete OAuth 2.1 deployment running on your machine, built to be taken apart. Click a lab, run it, then read what the servers actually did.',
    body, nav: NAV, active: 'Labs', brand: BRAND, footer: FOOT,
  });
}

function labPage({ lab, session, policy, message }) {
  const teaches = lab.teaches.map((t) => `<li>${t}</li>`).join('');
  const actions = (lab.actions || []).map((key) => {
    const meta = ACTION_LABELS[key] || { label: key, hint: '' };
    const needsToken = !['m2m', 'legacy-password', 'legacy-implicit'].includes(key);
    const disabled = needsToken && !(session.tokens && session.tokens.access_token);
    return `<form method="POST" action="/labs/${lab.id}/action" style="margin:0 0 10px">
      <input type="hidden" name="action" value="${escapeHtml(key)}">
      <button class="btn sm ${meta.danger ? 'danger' : 'ghost'}" type="submit" ${disabled ? 'disabled title="Run the lab first to get a token"' : ''}>${escapeHtml(meta.label)}</button>
      <span class="muted tiny" style="margin-left:8px">${escapeHtml(meta.hint)}</span>
    </form>`;
  }).join('');

  let policyBanner = '';
  if (lab.policyHint) {
    const current = policy ? policy[lab.policyHint.key] : null;
    const satisfied = lab.policyHint.wantOff ? current === false : current === true;
    policyBanner = note(
      `This lab is most instructive with <span class="mono">${escapeHtml(lab.policyHint.key)}</span> set to
       <b>${lab.policyHint.wantOff ? 'off' : 'on'}</b>. It is currently
       <b>${current === null ? 'unknown' : current ? 'on' : 'off'}</b>.
       ${satisfied ? 'You are set &mdash; run it.' : `<a href="http://localhost:9000/policy" target="_blank">Flip it on the policy page</a>, then come back and run the lab twice: once each way.`}`,
      satisfied ? 'good' : 'warn');
  }

  const startForm = lab.start ? `
  <form method="POST" action="/labs/${lab.id}/start">
    <div class="row">
      <button class="btn" type="submit">Run Lab ${lab.num}</button>
      <span class="muted small">Sends you to the authorization server. Sign in as
      <span class="mono">alice / wonderland</span>${lab.id === '07-scopes-and-consent' ? ' or <span class="mono">bob / builder</span>' : ''}.</span>
    </div>
    ${lab.id === '07-scopes-and-consent' ? `<label class="field">Scopes to request
      <input type="text" name="scope" value="${escapeHtml(lab.start.scope)}">
    </label>` : ''}
  </form>` : '';

  const request = lab.start ? kvTable({
    client_id: lab.start.client,
    response_type: 'code',
    scope: lab.start.scope,
    code_challenge_method: lab.start.pkce === 'none' ? '(omitted on purpose)' : lab.start.pkce,
    state: lab.start.state ? '(random, checked on return)' : '(omitted on purpose)',
    nonce: lab.start.nonce ? '(random, echoed in the ID token)' : '(not requested)',
    redirect_uri: lab.start.tamperRedirect ? '(tampered on purpose)' : 'registered value',
  }) : '<p class="muted small">This lab makes no browser redirect &mdash; it is a direct back-channel call.</p>';

  const body = `
${message ? note(message.html, message.kind) : ''}
${policyBanner}
${card(`
  <div class="row"><span class="pill ${LEVEL_PILL[lab.level] || 'mute'}">${escapeHtml(lab.level)}</span>
  <span class="muted tiny mono">LAB ${lab.num}</span></div>
  <h2 style="margin:8px 0 2px">${escapeHtml(lab.title)}</h2>
  <p class="lede" style="font-size:15px">${escapeHtml(lab.tagline)}</p>
  <h3>What this lab shows</h3>
  <ul class="small" style="padding-left:20px;line-height:1.75">${teaches}</ul>
  ${startForm}
`)}
${lab.start ? card(`<h3>The authorization request this will build</h3>${request}`, 'tight') : ''}
${actions ? card(`<h3>Actions</h3>${actions}${tokenBanner(session)}`) : ''}
${lab.followUp ? note(lab.followUp, 'warn') : ''}
${session.steps && session.steps.length ? card(`<div class="row"><h3 style="margin:0">Latest run</h3>
  <a class="btn sm ghost" href="/flow" style="margin-left:auto">Full flow inspector</a></div>
  ${renderSteps(session.steps.slice(-4))}`) : ''}
<p><a href="/">&larr; All labs</a></p>`;

  return page({ title: `Lab ${lab.num}: ${lab.title}`, body, nav: NAV, active: 'Labs', brand: BRAND, footer: FOOT });
}

const STATUS_CLASS = { done: 'done', fail: 'fail', warn: 'fail', info: 'pending', pending: 'pending' };

function renderSteps(steps) {
  if (!steps || !steps.length) return '<p class="muted small">Nothing recorded yet.</p>';
  return `<ol class="steps">${steps.map((s) => {
    const detail = [];
    if (s.request) {
      detail.push(`<div class="row tiny" style="margin-top:6px">${pill(s.request.method.toLowerCase(), s.request.method === 'GET' ? 'get' : 'post')}
        <span class="mono break">${escapeHtml(s.request.url)}</span></div>`);
      if (s.request.params) detail.push(kvTable(s.request.params, { highlight: s.request.highlight || [] }));
      if (s.request.curl) detail.push(pre(s.request.curl, 'wrapped'));
    }
    if (s.response) {
      const ok = s.response.status >= 200 && s.response.status < 300;
      detail.push(`<div class="row tiny" style="margin-top:6px">${pill(`HTTP ${s.response.status}`, ok ? 'ok' : 'err')}</div>`);
      if (s.response.body !== undefined) detail.push(preJson(s.response.body));
    }
    if (s.why) detail.push(`<p class="small muted" style="margin:6px 0 0">${s.why}</p>`);
    return `<li class="${STATUS_CLASS[s.status] || 'pending'}">
      <h4>${escapeHtml(s.title)} ${s.status === 'fail' ? pill('refused', 'err') : s.status === 'done' ? pill('ok', 'ok') : ''}</h4>
      <span class="when">${escapeHtml(s.at)}</span>
      ${detail.join('')}
    </li>`;
  }).join('')}</ol>`;
}

function flowPage(session) {
  const completed = [...new Set((session.steps || []).flatMap((s) => s.highlight || []))];
  const body = `
${card(`<div class="row"><h2 style="margin:0">Flow inspector</h2>
  <form method="POST" action="/reset" style="margin-left:auto"><button class="btn sm danger" type="submit">Clear session &amp; tokens</button></form></div>
  <p class="small muted">Every request this client made, in order, with the exact parameters. Compare it against the diagram.</p>
  ${tokenBanner(session)}`)}
${card(`<h3>Where you got to</h3>${authCodePkceDiagram(completed, 860)}`)}
${card(`<h3>Recorded steps</h3>${renderSteps(session.steps)}`)}
${card(`<h3>Server-side view</h3>
  <p class="small muted">The same events from the authorization server's perspective, including every rule it checked:</p>
  <p><a class="btn ghost" href="http://localhost:9000/trace" target="_blank">Open the live AS trace</a>
     <a class="btn ghost" href="http://localhost:9000/state" target="_blank">Open the AS state inspector</a></p>`, 'tight')}`;
  return page({ title: 'Flow inspector', body, nav: NAV, active: 'Flow', brand: BRAND, footer: FOOT });
}

function tokensPage({ session, decoded, apiResult }) {
  const t = session.tokens || {};
  const body = `
${card(`<div class="row"><h2 style="margin:0">Tokens held by this client</h2>
  <form method="POST" action="/reset" style="margin-left:auto"><button class="btn sm danger" type="submit">Discard</button></form></div>
  ${tokenBanner(session)}`)}
${!t.access_token ? note('No tokens yet. Run <a href="/">Lab 01</a>.', 'warn') : ''}
${t.access_token ? card(`
  <h3>Access token <span class="pill ok">for the API</span></h3>
  ${pre(t.access_token, 'wrapped')}
  <p class="small muted">Three base64url segments: header, payload, signature. Only the signature is opaque &mdash;
  anyone can read the payload, so never put a secret in it.</p>
  <h3>Header</h3>${preJson(decoded.access && decoded.access.header)}
  <h3>Payload</h3>${preJson(decoded.access && decoded.access.payload)}
  ${kvTable({
    aud: 'which API may accept this token',
    scope: 'what it is allowed to do there',
    sub: 'whose data it acts on',
    client_id: 'which app is holding it',
    exp: 'when it stops working',
    jti: 'unique id, used for revocation',
  })}
`) : ''}
${t.id_token ? card(`
  <h3>ID token <span class="pill mute">for this client</span></h3>
  ${pre(t.id_token, 'wrapped')}
  <h3>Payload</h3>${preJson(decoded.id && decoded.id.payload)}
  ${note('<b>aud is the client_id, not the API.</b> An ID token answers &ldquo;who signed in?&rdquo; It is not a key to anything. Sending it as a Bearer token is the single most common OAuth mistake &mdash; Lab 08 proves the API rejects it.')}
`) : ''}
${t.refresh_token ? card(`
  <h3>Refresh token <span class="pill warn">long-lived</span></h3>
  ${pre(t.refresh_token, 'wrapped')}
  <p class="small muted">Opaque and random &mdash; there is nothing to decode. The AS stores only its hash.
  It is the most dangerous thing this client holds, which is why rotation and reuse detection exist.</p>
`) : ''}
${t.access_token ? card(`
  <h3>Call the API</h3>
  <div class="row">
    ${['GET /api/me', 'GET /api/accounts', 'POST /api/payments', 'GET /api/accounts-introspected'].map((ep) => {
      const [method, path] = ep.split(' ');
      return `<form method="POST" action="/api/call" style="margin:0">
        <input type="hidden" name="method" value="${method}"><input type="hidden" name="path" value="${path}">
        <button class="btn sm ghost" type="submit">${escapeHtml(ep)}</button></form>`;
    }).join('')}
  </div>
  ${apiResult ? `<div style="margin-top:12px">${pill(`HTTP ${apiResult.status}`, apiResult.status < 300 ? 'ok' : 'err')}
    ${preJson(apiResult.body)}</div>` : ''}
`) : ''}`;
  return page({ title: 'Token inspector', body, nav: NAV, active: 'Tokens', brand: BRAND, footer: FOOT });
}

function conceptsPage() {
  const body = `
${card(`<h2 style="margin-top:0">The problem OAuth solves</h2>
  <p>An application wants data that belongs to you, held by another service. The naive solution is to
  ask for your password &mdash; which hands over <b>everything, forever, with no record and no way back</b>.</p>
  ${note('<b>OAuth is delegated authorization.</b> You prove who you are to the party that already knows you (the identity provider), and that party issues the application a narrow, expiring, revocable token. The application never sees your password.', 'good')}`)}
${card(`<h2>The four roles</h2>
  <table><thead><tr><th>Role</th><th>Who that is here</th><th>Holds</th></tr></thead><tbody>
  <tr><td><b>Resource Owner</b></td><td>You &mdash; alice or bob</td><td class="tiny">The authority being delegated</td></tr>
  <tr><td><b>Client</b></td><td>This app on :9020</td><td class="tiny">A client_id, maybe a secret, and tokens</td></tr>
  <tr><td><b>Authorization Server</b></td><td>:9000</td><td class="tiny">User accounts, consent records, signing keys</td></tr>
  <tr><td><b>Resource Server</b></td><td>:9010</td><td class="tiny">The data, plus the AS's public keys</td></tr>
  </tbody></table>`)}
${card(`<h2>Front channel vs back channel</h2>
  <div class="grid two">
    <div><h3 style="margin-top:0">Front channel ${pill('untrusted', 'warn')}</h3>
      <p class="small">Through the user's browser, by redirect. Everything is visible in the URL bar, browser
      history, server logs and <span class="mono">Referer</span> headers. Any app on the device may be able to see it.</p>
      <p class="small"><b>May carry:</b> client_id, scope, state, code_challenge, and the authorization code.<br>
      <b>Must never carry:</b> client secrets, the code_verifier, or an access token.</p></div>
    <div><h3 style="margin-top:0">Back channel ${pill('trusted', 'ok')}</h3>
      <p class="small">A direct HTTPS call from the client's server to the authorization server. The browser
      cannot see it, log it, or interfere with it.</p>
      <p class="small"><b>Carries:</b> the code, the code_verifier, client credentials, and the tokens that come back.</p></div>
  </div>
  ${note('Every design decision in OAuth 2.1 follows from this split. PKCE exists precisely because the code has to cross the untrusted channel.')}`)}
${card(`<h2>PKCE in one page</h2>
  <p class="small">Proof Key for Code Exchange, RFC 7636. Three lines of arithmetic that turn a stealable code into a useless one.</p>
  ${pre(`# Before redirecting the user, the client generates a secret:
code_verifier  = base64url(random 32 bytes)            # 43 chars, kept private

# and derives a value that is safe to publish:
code_challenge = base64url(sha256(code_verifier))      # 43 chars, sent in the URL

# /authorize  -->  code_challenge travels through the browser
# /token      -->  code_verifier travels on the back channel

# The authorization server's check:
sha256(presented_code_verifier) == stored_code_challenge ?`)}
  <ul class="small" style="padding-left:20px;line-height:1.8">
    <li>An attacker who steals the code never saw the verifier, and cannot compute it from the challenge.</li>
    <li>An attacker who steals the <i>challenge</i> gains nothing &mdash; it is a hash, not a credential.</li>
    <li>OAuth 2.1 requires this for <b>every</b> client, including confidential ones with secrets.</li>
    <li>Always <span class="mono">S256</span>. Never <span class="mono">plain</span>, where the "hash" is the verifier itself.</li>
  </ul>
  <p class="small">Try it: <a href="http://localhost:9000/pkce/explain" target="_blank">/pkce/explain</a> on the AS shows the transform step by step.</p>`)}
${card(`<h2>What OAuth 2.1 changed</h2>
  <table><thead><tr><th>OAuth 2.0 (2012)</th><th>OAuth 2.1</th><th>Why</th></tr></thead><tbody>
  <tr><td>PKCE optional, for mobile</td><td><b>PKCE required for all clients</b></td><td class="tiny">Codes leak in ways nobody predicted; the defence is cheap.</td></tr>
  <tr><td>Implicit grant returns tokens in the URL</td><td><b>Removed</b></td><td class="tiny">Tokens in fragments end up in history, logs and Referer headers.</td></tr>
  <tr><td>Password grant</td><td><b>Removed</b></td><td class="tiny">The app handles the real password: no consent, no MFA, no federation.</td></tr>
  <tr><td>redirect_uri matching left vague</td><td><b>Exact string match</b></td><td class="tiny">Wildcards let attackers redirect codes to themselves.</td></tr>
  <tr><td>Bearer tokens in query strings allowed</td><td><b>Header only</b></td><td class="tiny">Query strings are logged everywhere.</td></tr>
  <tr><td>Refresh tokens static</td><td><b>Rotate, or bind to the client</b></td><td class="tiny">A stolen static refresh token is permanent access.</td></tr>
  </tbody></table>
  ${note('OAuth 2.1 adds almost nothing new. It deletes what was dangerous and makes the best existing practice mandatory. If you know 2.0 plus the security BCP (RFC 9700), you already know 2.1.')}`)}
${card(`<h3>Go deeper</h3>
  <p class="small">The developer guide PDF in <span class="mono">oauth-simulator/dev-guide/dev-guide.pdf</span> covers all of
  this with full-page diagrams, a worked PKCE example, token anatomy, and a security checklist.</p>`, 'tight')}`;
  return page({
    title: 'Concepts',
    subtitle: 'The fundamentals, in the order that makes them make sense. Read alongside the labs.',
    body, nav: NAV, active: 'Concepts', brand: BRAND, footer: FOOT,
  });
}

function callbackErrorPage({ error, description, params }) {
  const body = `${card(`
  <h2><span class="pill err">${escapeHtml(error)}</span></h2>
  <p style="font-size:16px">${escapeHtml(description)}</p>
  <h3>Callback parameters received</h3>${kvTable(params)}
  <p><a class="btn ghost" href="/flow">See the flow inspector</a> <a class="btn ghost" href="/">All labs</a></p>`)}`;
  return page({ title: `Callback: ${error}`, body, nav: NAV, brand: BRAND, footer: FOOT, wrapClass: 'narrow' });
}

module.exports = { homePage, labPage, flowPage, tokensPage, conceptsPage, callbackErrorPage, renderSteps, NAV, BRAND, FOOT };
