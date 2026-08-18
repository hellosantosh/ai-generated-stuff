/**
 * HTTP client for the Brokerage REST API. Node 18+ built-ins only.
 *
 * Three decisions worth calling out, because getting them wrong is expensive
 * in this domain:
 *
 *   - Money never becomes a JavaScript number. `Number('412.50')` is fine, but
 *     `0.1 + 0.2 !== 0.3`, and share counts above 2^53 are not the only way to
 *     lose precision -- any arithmetic on a price can drift. The API sends
 *     decimals as strings so they survive the round trip; keep them as strings
 *     and do arithmetic with the `Dec` helper below (or a library such as
 *     decimal.js in production).
 *
 *   - Retries are scoped by method. GET is safe and always retryable. POST is
 *     retryable only because every order carries an Idempotency-Key -- without
 *     one, retrying a timed-out order is how a customer ends up with two
 *     positions instead of one.
 *
 *   - A 401 triggers exactly one refresh-and-retry. Looping on 401 turns a
 *     revoked grant into a request storm against the auth server.
 */

import { randomUUID } from 'node:crypto';
import { getValidTokens, OAuthError, refreshTokens, TokenStore } from './pkce-auth.mjs';

export const API_BASE = 'https://api.sandbox.brokerage.example.com/v1';
const USER_AGENT = 'brokerage-node-example/1.0';

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/**
 * An RFC 9457 problem response.
 *
 * Branch on `.type`, never on `.title` or `.detail` -- those are human-facing
 * prose and get reworded without notice. `.traceId` is what support will ask
 * for, so log it on every failure.
 */
export class ApiError extends Error {
  constructor(status, problem = {}) {
    super(
      `HTTP ${status} ${problem.title ?? 'Unknown error'}: ${problem.detail ?? ''} ` +
        `(trace ${problem.traceId ?? 'n/a'})`,
    );
    this.name = 'ApiError';
    this.status = status;
    this.problem = problem;
    this.type = problem.type ?? 'about:blank';
    this.title = problem.title ?? 'Unknown error';
    this.detail = problem.detail ?? '';
    this.traceId = problem.traceId;
    this.errors = problem.errors ?? [];
  }

  get isRetryable() {
    return [429, 502, 503, 504].includes(this.status);
  }
}

// ---------------------------------------------------------------------------
// Decimal arithmetic on strings
// ---------------------------------------------------------------------------

/**
 * Minimal exact decimal arithmetic over BigInt, enough for the examples here.
 *
 * Production code should use decimal.js or big.js. This exists to make the
 * point concrete: `Number(price) * Number(qty)` is wrong in a way that is
 * invisible until it shows up in a reconciliation break.
 */
export const Dec = {
  scaleOf: (s) => (s.split('.')[1] ?? '').length,

  toBigInt(s, scale) {
    const negative = s.startsWith('-');
    const [whole, frac = ''] = s.replace('-', '').split('.');
    const digits = (whole + frac.padEnd(scale, '0')).slice(0, whole.length + scale) || '0';
    return BigInt(negative ? `-${digits}` : digits);
  },

  fromBigInt(v, scale) {
    const negative = v < 0n;
    const digits = (negative ? -v : v).toString().padStart(scale + 1, '0');
    const whole = digits.slice(0, digits.length - scale);
    const frac = scale ? `.${digits.slice(digits.length - scale)}` : '';
    return `${negative ? '-' : ''}${whole}${frac}`;
  },

  add(a, b) {
    const scale = Math.max(Dec.scaleOf(a), Dec.scaleOf(b));
    return Dec.fromBigInt(Dec.toBigInt(a, scale) + Dec.toBigInt(b, scale), scale);
  },

  mul(a, b) {
    const scaleA = Dec.scaleOf(a);
    const scaleB = Dec.scaleOf(b);
    return Dec.fromBigInt(Dec.toBigInt(a, scaleA) * Dec.toBigInt(b, scaleB), scaleA + scaleB);
  },

  cmp(a, b) {
    const scale = Math.max(Dec.scaleOf(a), Dec.scaleOf(b));
    const bigA = Dec.toBigInt(a, scale);
    const bigB = Dec.toBigInt(b, scale);
    return bigA < bigB ? -1 : bigA > bigB ? 1 : 0;
  },

  abs: (a) => (a.startsWith('-') ? a.slice(1) : a),

  /** Format for display only. Never feed the result back into a request. */
  format(s, places = 2) {
    const negative = s.startsWith('-');
    const [whole, frac = ''] = Dec.abs(s).split('.');
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    const decimals = places ? `.${frac.padEnd(places, '0').slice(0, places)}` : '';
    return `${negative ? '-' : ''}${grouped}${decimals}`;
  },
};

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export class BrokerageClient {
  constructor({ baseUrl = API_BASE, store = new TokenStore(), maxRetries = 4 } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.store = store;
    this.maxRetries = maxRetries;
    this.tokens = null;
  }

  // -- auth -----------------------------------------------------------------

  async #accessToken() {
    if (!this.tokens || this.tokens.expired) {
      this.tokens = await getValidTokens(this.store);
    }
    return this.tokens.accessToken;
  }

  /**
   * Refresh after a 401 even if the token looked locally valid -- it can be
   * revoked server-side (the customer disconnects the app, or security
   * invalidates a family) long before it expires.
   */
  async #forceRefresh() {
    if (this.tokens?.refreshToken) {
      try {
        this.tokens = await refreshTokens(this.tokens);
        await this.store.save(this.tokens);
        return;
      } catch (err) {
        if (!(err instanceof OAuthError)) throw err;
      }
    }
    this.tokens = await getValidTokens(this.store);
  }

  // -- transport ------------------------------------------------------------

  /**
   * Issue one request. Resolves to `{ body, headers, status }`.
   *
   * `path` may be relative to the API base or an absolute URL, so a HATEOAS
   * link can be passed straight through without parsing.
   */
  async request(method, path, {
    params,
    body,
    headers = {},
    idempotencyKey,
    ifMatch,
    contentType = 'application/json',
  } = {}) {
    let url = path.startsWith('http') ? path : `${this.baseUrl}${path}`;

    if (params) {
      const search = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value == null) continue;
        // Arrays become repeated parameters: status=NEW&status=FILLED
        for (const item of Array.isArray(value) ? value : [value]) search.append(key, item);
      }
      if ([...search].length) url += `?${search}`;
    }

    const payload = body === undefined ? undefined : JSON.stringify(body);

    let attempt = 0;
    let refreshed = false;

    for (;;) {
      attempt += 1;

      const requestHeaders = {
        Authorization: `Bearer ${await this.#accessToken()}`,
        Accept: 'application/json, application/problem+json',
        'User-Agent': USER_AGENT,
        ...headers,
      };
      if (payload !== undefined) requestHeaders['Content-Type'] = contentType;
      if (idempotencyKey) requestHeaders['Idempotency-Key'] = idempotencyKey;
      if (ifMatch) requestHeaders['If-Match'] = ifMatch;

      let response;
      try {
        response = await fetch(url, {
          method,
          headers: requestHeaders,
          body: payload,
          signal: AbortSignal.timeout(30_000),
        });
      } catch (err) {
        // A transport failure is the dangerous case for writes: the order may
        // well have been accepted. Only retry when idempotency makes it safe.
        const safe = method === 'GET' || method === 'HEAD' || Boolean(idempotencyKey);
        if (safe && attempt <= this.maxRetries) {
          await sleep(backoff(attempt));
          continue;
        }
        throw new ApiError(0, { title: 'Network error', detail: String(err) });
      }

      if (response.status === 304) {
        return { body: null, headers: response.headers, status: 304 };
      }

      if (response.ok) {
        const text = await response.text();
        return {
          body: text ? JSON.parse(text) : null,
          headers: response.headers,
          status: response.status,
        };
      }

      // One refresh-and-retry on 401, then give up.
      if (response.status === 401 && !refreshed) {
        refreshed = true;
        await this.#forceRefresh();
        continue;
      }

      const text = await response.text();
      let problem;
      try {
        problem = text ? JSON.parse(text) : {};
      } catch {
        problem = { title: 'Non-JSON error body', detail: text.slice(0, 400) };
      }
      const error = new ApiError(response.status, problem);

      const retryable =
        attempt <= this.maxRetries &&
        error.isRetryable &&
        (['GET', 'HEAD', 'DELETE'].includes(method) || Boolean(idempotencyKey));

      if (!retryable) throw error;

      const retryAfter = Number(response.headers.get('Retry-After'));
      await sleep(Number.isFinite(retryAfter) && retryAfter > 0
        ? retryAfter * 1000 + Math.random() * 1000
        : backoff(attempt));
    }
  }

  // -- verbs ----------------------------------------------------------------

  async get(path, options) {
    return (await this.request('GET', path, options)).body;
  }

  async getWithHeaders(path, options) {
    return this.request('GET', path, options);
  }

  async post(path, body, options) {
    return (await this.request('POST', path, { ...options, body })).body;
  }

  async patch(path, body, options) {
    return (
      await this.request('PATCH', path, {
        ...options,
        body,
        contentType: 'application/merge-patch+json',
      })
    ).body;
  }

  async delete(path, options) {
    return (await this.request('DELETE', path, options)).body;
  }

  // -- hypermedia -----------------------------------------------------------

  /**
   * Read one link relation, or undefined if the action is unavailable.
   *
   * This is the intended way to navigate. A missing `cancel` link means the
   * order is not cancellable right now -- more reliable than reading `status`,
   * because new statuses can appear without a version bump but the link
   * contract holds.
   */
  static link(resource, rel) {
    return resource?._links?.[rel]?.href;
  }

  static can(resource, rel) {
    return Boolean(resource?._links?.[rel]);
  }

  link(resource, rel) {
    return BrokerageClient.link(resource, rel);
  }

  can(resource, rel) {
    return BrokerageClient.can(resource, rel);
  }

  async follow(resource, rel, options) {
    const href = this.link(resource, rel);
    if (!href) throw new Error(`No '${rel}' link on this resource; the action is unavailable.`);
    return this.get(href, options);
  }

  /**
   * Async-iterate every item across pages by following `next` links.
   *
   * It follows the server's `next` href verbatim rather than constructing
   * cursors -- the cursor encoding is explicitly not part of the contract.
   */
  async *paginate(path, options) {
    let page = await this.get(path, options);
    for (;;) {
      yield* page.items ?? [];
      const next = this.link(page, 'next');
      if (!next) return;
      page = await this.get(next);
    }
  }

  async collect(path, options) {
    const out = [];
    for await (const item of this.paginate(path, options)) out.push(item);
    return out;
  }

  // -- streaming ------------------------------------------------------------

  /**
   * Consume the SSE order event stream.
   *
   * Reconnects automatically and resumes from the last event id, so nothing is
   * lost across a dropped connection inside the 5-minute retention window.
   * Deduplicate on `eventId`: delivery is at-least-once.
   */
  async *streamOrderEvents(account, { signal } = {}) {
    const url = `${this.link(account, 'orders')}/events`;
    let lastEventId;

    for (;;) {
      const headers = {
        Authorization: `Bearer ${await this.#accessToken()}`,
        Accept: 'text/event-stream',
        'User-Agent': USER_AGENT,
      };
      if (lastEventId) headers['Last-Event-ID'] = lastEventId;

      let response;
      try {
        response = await fetch(url, { headers, signal });
      } catch (err) {
        if (signal?.aborted) return;
        await sleep(backoff(1));
        continue;
      }

      if (!response.ok) {
        if (response.status === 401) {
          await this.#forceRefresh();
          continue;
        }
        throw new ApiError(response.status, await response.json().catch(() => ({})));
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by a blank line.
          let split;
          while ((split = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, split);
            buffer = buffer.slice(split + 2);

            const event = {};
            for (const line of frame.split('\n')) {
              if (line.startsWith(':')) continue;             // heartbeat comment
              const colon = line.indexOf(':');
              if (colon === -1) continue;
              const field = line.slice(0, colon);
              const data = line.slice(colon + 1).trimStart();
              event[field] = field === 'data' ? (event.data ?? '') + data : data;
            }

            if (event.id) lastEventId = event.id;
            if (event.data) yield JSON.parse(event.data);
          }
        }
      } catch (err) {
        if (signal?.aborted) return;
      }

      // Server closed or the connection dropped; reconnect and resume.
      if (signal?.aborted) return;
      await sleep(1000);
    }
  }

  // -- helpers --------------------------------------------------------------

  /**
   * One key per order *intent*.
   *
   * Generate it before the first attempt and reuse it across every retry of
   * that intent. Generating a fresh key inside a retry loop defeats the whole
   * mechanism.
   */
  static newIdempotencyKey() {
    return randomUUID();
  }

  newIdempotencyKey() {
    return randomUUID();
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Exponential backoff with full jitter.
 *
 * Full jitter (random between 0 and the cap) rather than fixed backoff: when a
 * venue hiccups every client retries at once, and synchronized retries are how
 * a brief blip becomes an outage.
 */
const backoff = (attempt) => Math.random() * Math.min(2 ** attempt * 500, 20_000);
