'use strict';
/**
 * A deliberately tiny HTTP layer built only on Node core modules.
 *
 * Why no Express? So that every byte of the OAuth 2.1 protocol in this
 * simulator is visible in this repository. There is no middleware doing
 * something clever behind your back -- if a header, cookie or form field
 * matters to the protocol, you can read the code that handles it.
 */
const http = require('node:http');
const { URL } = require('node:url');

const MAX_BODY = 1024 * 256;

/** Read and parse a request body (form-encoded or JSON). */
function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error('body too large'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      const type = (req.headers['content-type'] || '').split(';')[0].trim();
      try {
        if (!raw) return resolve({ raw, fields: {} });
        if (type === 'application/json') return resolve({ raw, fields: JSON.parse(raw) });
        if (type === 'application/x-www-form-urlencoded') {
          const fields = {};
          for (const [k, v] of new URLSearchParams(raw)) fields[k] = v;
          return resolve({ raw, fields });
        }
        return resolve({ raw, fields: {} });
      } catch (err) {
        reject(err);
      }
    });
    req.on('error', reject);
  });
}

function parseCookies(req) {
  const out = {};
  const header = req.headers.cookie;
  if (!header) return out;
  for (const part of header.split(';')) {
    const idx = part.indexOf('=');
    if (idx === -1) continue;
    out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
  }
  return out;
}

function serializeCookie(name, value, opts = {}) {
  const bits = [`${name}=${encodeURIComponent(value)}`];
  bits.push(`Path=${opts.path || '/'}`);
  if (opts.maxAge != null) bits.push(`Max-Age=${opts.maxAge}`);
  if (opts.httpOnly !== false) bits.push('HttpOnly');
  // SameSite=Lax is what a real client/AS wants: the OAuth redirect back from
  // the authorization server is a top-level GET navigation, so Lax cookies are
  // still sent. `Strict` would break the callback; `None` would need Secure.
  bits.push(`SameSite=${opts.sameSite || 'Lax'}`);
  if (opts.secure) bits.push('Secure');
  return bits.join('; ');
}

/** The response helper handed to every route. */
class Res {
  constructor(res) {
    this.raw = res;
    this.headers = {};
    this.cookies = [];
  }
  header(name, value) { this.headers[name] = value; return this; }
  cookie(name, value, opts) { this.cookies.push(serializeCookie(name, value, opts)); return this; }
  clearCookie(name, opts = {}) { return this.cookie(name, '', { ...opts, maxAge: 0 }); }
  #send(status, type, payload, extra = {}) {
    if (this.raw.writableEnded) return this;
    const headers = { 'content-type': type, ...this.headers, ...extra };
    if (this.cookies.length) headers['set-cookie'] = this.cookies;
    this.raw.writeHead(status, headers);
    this.raw.end(payload);
    return this;
  }
  html(body, status = 200) { return this.#send(status, 'text/html; charset=utf-8', body); }
  text(body, status = 200) { return this.#send(status, 'text/plain; charset=utf-8', body); }
  /** OAuth responses must never be cached -- they carry credentials. */
  json(obj, status = 200) {
    return this.#send(status, 'application/json; charset=utf-8', JSON.stringify(obj, null, 2), {
      'cache-control': 'no-store',
      pragma: 'no-cache',
    });
  }
  redirect(location, status = 302) { return this.#send(status, 'text/plain; charset=utf-8', `Redirecting to ${location}`, { location }); }
  sse() {
    this.raw.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-store',
      connection: 'keep-alive',
      'x-accel-buffering': 'no',
    });
    this.raw.write(': connected\n\n');
    return {
      send: (event, data) => {
        if (this.raw.writableEnded) return false;
        this.raw.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
        return true;
      },
      onClose: (fn) => this.raw.on('close', fn),
    };
  }
}

/**
 * Minimal router: exact paths plus `:param` segments and a `*` suffix.
 */
class App {
  constructor({ name, log } = {}) {
    this.name = name || 'app';
    this.log = log || (() => {});
    this.routes = [];
  }
  #add(method, pattern, handler) {
    const segments = pattern.split('/').filter(Boolean);
    this.routes.push({ method, pattern, segments, handler });
    return this;
  }
  get(p, h) { return this.#add('GET', p, h); }
  post(p, h) { return this.#add('POST', p, h); }
  options(p, h) { return this.#add('OPTIONS', p, h); }
  all(p, h) { return this.#add('*', p, h); }

  #match(method, pathname) {
    const parts = pathname.split('/').filter(Boolean);
    for (const route of this.routes) {
      if (route.method !== '*' && route.method !== method) continue;
      const params = {};
      let ok = true;
      const wildcard = route.segments[route.segments.length - 1] === '*';
      const expected = wildcard ? route.segments.slice(0, -1) : route.segments;
      if (wildcard ? parts.length < expected.length : parts.length !== expected.length) continue;
      for (let i = 0; i < expected.length; i += 1) {
        const seg = expected[i];
        if (seg.startsWith(':')) params[seg.slice(1)] = decodeURIComponent(parts[i]);
        else if (seg !== parts[i]) { ok = false; break; }
      }
      if (ok) return { route, params };
    }
    return null;
  }

  async handle(req, res) {
    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    const response = new Res(res);
    const found = this.#match(req.method, url.pathname);
    if (!found) return response.json({ error: 'not_found', path: url.pathname }, 404);
    const query = {};
    for (const [k, v] of url.searchParams) query[k] = v;
    const ctx = {
      req,
      res: response,
      url,
      query,
      params: found.params,
      cookies: parseCookies(req),
      method: req.method,
      ip: req.socket.remoteAddress,
      body: { raw: '', fields: {} },
    };
    try {
      if (req.method === 'POST') ctx.body = await readBody(req);
      await found.route.handler(ctx);
    } catch (err) {
      this.log('error', `unhandled error on ${req.method} ${url.pathname}`, { message: err.message, stack: err.stack });
      if (!res.writableEnded) response.json({ error: 'server_error', message: err.message }, 500);
    }
  }

  listen(port, onReady) {
    const server = http.createServer((req, res) => this.handle(req, res));
    server.listen(port, '0.0.0.0', () => onReady && onReady(port));
    return server;
  }
}

/** HTTP Basic credentials, as used by `client_secret_basic` client auth. */
function parseBasicAuth(req) {
  const header = req.headers.authorization || '';
  if (!header.toLowerCase().startsWith('basic ')) return null;
  const decoded = Buffer.from(header.slice(6).trim(), 'base64').toString('utf8');
  const idx = decoded.indexOf(':');
  if (idx === -1) return null;
  // RFC 6749 §2.3.1 requires form-urlencoding the id and secret before base64.
  return {
    clientId: decodeURIComponent(decoded.slice(0, idx)),
    clientSecret: decodeURIComponent(decoded.slice(idx + 1)),
  };
}

function bearerToken(req) {
  const header = req.headers.authorization || '';
  if (!header.toLowerCase().startsWith('bearer ')) return null;
  return header.slice(7).trim();
}

/** Small JSON fetch helper with a timeout, used for back-channel calls. */
async function fetchJson(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs || 8000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const text = await response.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch { /* non-JSON body */ }
    return { status: response.status, ok: response.ok, json, text, headers: response.headers };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { App, Res, readBody, parseCookies, serializeCookie, parseBasicAuth, bearerToken, fetchJson };
