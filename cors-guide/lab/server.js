'use strict';
// ===========================================================================
// cors-guide lab - server.js                  run:  node server.js
//
// Two origins on your own machine, and nothing else:
//
//   http://localhost:8080   the Harbor Pay web app (a page with lab buttons)
//   http://localhost:8081   the Harbor Pay API, which applies the CORS policy
//
// The CORS policy is the POLICY object below. Every lab in the book asks you
// to change one line of it, restart, and watch the browser react in DevTools.
//
//   node server.js           start both origins
//   node server.js check     print the headers the policy produces, and exit
//
// Zero dependencies: Node 18 or later, core modules only.
// ===========================================================================
const http = require('node:http');

const APP_PORT = 8080;
const API_PORT = 8081;

// --- the policy ------------------------------------------------------------
// An exact allowlist. No patterns, no echoing, no "*" alongside credentials.
const POLICY = {
  allowedOrigins: ['http://localhost:8080'],
  allowMethods: ['GET', 'POST', 'PUT', 'DELETE'],
  allowHeaders: ['content-type', 'x-request-id'],
  exposeHeaders: ['x-request-id'],
  allowCredentials: true,
  maxAgeSeconds: 600,
};

// --- the CORS middleware -----------------------------------------------------
// Returns true when it has fully answered the request (a preflight).
function applyCors(req, res) {
  const origin = req.headers.origin;

  // The answer depends on the Origin header, so any cache in between must
  // key on it too. Sent on every response, allowed or not.
  res.setHeader('Vary', 'Origin');

  const allowed = origin !== undefined && POLICY.allowedOrigins.includes(origin);

  if (allowed) {
    res.setHeader('Access-Control-Allow-Origin', origin);   // one exact origin
    if (POLICY.allowCredentials) res.setHeader('Access-Control-Allow-Credentials', 'true');
    if (POLICY.exposeHeaders.length) {
      res.setHeader('Access-Control-Expose-Headers', POLICY.exposeHeaders.join(', '));
    }
  }

  const isPreflight = req.method === 'OPTIONS'
    && req.headers['access-control-request-method'] !== undefined;
  if (!isPreflight) return false;

  if (allowed) {
    res.setHeader('Access-Control-Allow-Methods', POLICY.allowMethods.join(', '));
    res.setHeader('Access-Control-Allow-Headers', POLICY.allowHeaders.join(', '));
    res.setHeader('Access-Control-Max-Age', String(POLICY.maxAgeSeconds));
  }
  // A refused preflight is simply one without the permission headers.
  res.writeHead(204);
  res.end();
  return true;
}

// --- a request log, so you can see what reached the server -------------------
const LOG = [];
function record(req) {
  LOG.unshift({
    at: new Date().toISOString().slice(11, 19),
    method: req.method,
    path: req.url,
    origin: req.headers.origin || '(none)',
    preflightFor: req.headers['access-control-request-method'] || '',
    cookie: req.headers.cookie ? 'yes' : 'no',
  });
  LOG.length = Math.min(LOG.length, 40);
}

// --- the API origin ----------------------------------------------------------
const NOTES = [];
let nextId = 1;

function json(res, status, body, extra = {}) {
  res.writeHead(status, { 'content-type': 'application/json', ...extra });
  res.end(JSON.stringify(body, null, 2));
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; });
    req.on('end', () => resolve(data));
  });
}

const sessionOf = (req) => /(?:^|;\s*)hp_session=([^;]+)/.exec(req.headers.cookie || '');

const api = http.createServer(async (req, res) => {
  record(req);
  if (applyCors(req, res)) return;
  const url = new URL(req.url, 'http://localhost');
  const requestId = 'req-' + Math.random().toString(16).slice(2, 10);

  if (url.pathname === '/api/rates' && req.method === 'GET') {
    return json(res, 200, { USD: 1, EUR: 0.92, GBP: 0.79 }, { 'x-request-id': requestId });
  }

  if (url.pathname === '/login' && req.method === 'GET') {
    // Visit this URL directly in the browser to get a session cookie.
    res.setHeader('Set-Cookie', 'hp_session=demo-user; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax');
    return json(res, 200, { signedIn: 'demo-user', next: 'return to http://localhost:8080' });
  }

  if (url.pathname === '/api/profile' && req.method === 'GET') {
    const session = sessionOf(req);
    if (!session) return json(res, 401, { error: 'not signed in' });
    return json(res, 200, { user: session[1], plan: 'Harbor Pay Plus' },
      { 'x-request-id': requestId });
  }

  if (url.pathname === '/api/notes' && req.method === 'POST') {
    if (!(req.headers['content-type'] || '').startsWith('application/json')) {
      return json(res, 415, { error: 'send application/json' });
    }
    const note = { id: nextId++, text: String(JSON.parse(await readBody(req)).text || '') };
    NOTES.push(note);
    return json(res, 201, note, { 'x-request-id': requestId });
  }

  if (url.pathname === '/log') {
    return json(res, 200, LOG);
  }

  json(res, 404, { error: 'not found' });
});

// --- the app origin ----------------------------------------------------------
const PAGE = `<!doctype html>
<html lang="en-US"><head><meta charset="utf-8"><title>Harbor Pay - CORS lab</title>
<style>
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 760px; margin: 32px auto; padding: 0 16px; }
  button { font: inherit; margin: 4px 6px 4px 0; padding: 6px 12px; cursor: pointer; }
  pre { background: #f4f6fb; padding: 12px; border-radius: 6px; white-space: pre-wrap; }
  .ok { color: #0d7a52; } .no { color: #c0203f; }
</style></head><body>
<h1>Harbor Pay &middot; CORS lab</h1>
<p>This page is served from <b id="me"></b>. The API lives at <b>http://localhost:8081</b>.
Open DevTools (Network and Console tabs) before pressing a button.</p>
<button data-lab="1">Lab 1 &middot; GET public rates</button>
<button data-lab="2">Lab 2 &middot; POST JSON (preflight)</button>
<button data-lab="3">Lab 3 &middot; GET profile with cookie</button>
<button data-lab="4">Lab 4 &middot; read an exposed header</button>
<pre id="out">Results appear here.</pre>
<script>
const API = 'http://localhost:8081';
document.getElementById('me').textContent = location.origin;
const out = document.getElementById('out');
const show = (cls, text) => { out.className = cls; out.textContent = text; };

const LABS = {
  1: () => fetch(API + '/api/rates'),
  2: () => fetch(API + '/api/notes', { method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text: 'hello from ' + location.origin }) }),
  3: () => fetch(API + '/api/profile', { credentials: 'include' }),
  4: () => fetch(API + '/api/rates'),
};

async function run(n) {
  try {
    const res = await LABS[n]();
    const body = await res.text();
    const rid = res.headers.get('x-request-id');
    show('ok', 'Lab ' + n + ': status ' + res.status + '\\n'
      + 'x-request-id visible to script: ' + (rid || '(null)') + '\\n\\n' + body);
  } catch (err) {
    // The browser deliberately says almost nothing here. The Console has the detail.
    show('no', 'Lab ' + n + ': ' + err.name + ': ' + err.message
      + '\\nThe script cannot tell why. Check the Console and Network tabs.');
  }
}
document.querySelectorAll('button').forEach((b) => b.onclick = () => run(b.dataset.lab));
const auto = new URLSearchParams(location.search).get('auto');
if (auto) run(auto);
</script></body></html>`;

const app = http.createServer((req, res) => {
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(PAGE);
});

// --- `node server.js check`: the policy, as raw headers ------------------------
async function check() {
  await new Promise((r) => api.listen(API_PORT, r));
  const probe = async (label, origin, method, extra = {}) => {
    const headers = { origin, ...extra };
    const res = await fetch('http://localhost:' + API_PORT + '/api/notes',
      { method, headers });
    const got = [...res.headers].filter(([k]) => k.startsWith('access-control') || k === 'vary');
    console.log('\n' + label + '\n  ' + method + ' /api/notes  Origin: ' + origin
      + '\n  -> ' + res.status);
    for (const [k, v] of got) console.log('     ' + k + ': ' + v);
    if (!got.some(([k]) => k === 'access-control-allow-origin')) {
      console.log('     (no Access-Control-Allow-Origin: the browser will not share the response)');
    }
  };
  const pre = { 'access-control-request-method': 'POST',
    'access-control-request-headers': 'content-type' };
  await probe('Preflight from the app', 'http://localhost:8080', 'OPTIONS', pre);
  await probe('Preflight from an origin not on the list', 'http://127.0.0.1:8080', 'OPTIONS', pre);
  api.close();
}

if (process.argv[2] === 'check') {
  check();
} else {
  api.listen(API_PORT, () => app.listen(APP_PORT, () => {
    console.log('Harbor Pay app  http://localhost:' + APP_PORT);
    console.log('Harbor Pay API  http://localhost:' + API_PORT + '   (request log: /log)');
    console.log('Allowed origins: ' + POLICY.allowedOrigins.join(', '));
  }));
}
