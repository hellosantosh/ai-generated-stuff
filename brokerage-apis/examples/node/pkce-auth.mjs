/**
 * OAuth 2.1 authorization code flow with PKCE, for the Brokerage REST API.
 *
 * Node 18+ built-ins only -- no SDK is published for this API, and none is
 * needed. Uses global `fetch`, `node:crypto`, and `node:http`.
 *
 * What OAuth 2.1 changes, and why this file looks the way it does:
 *
 *   - PKCE is mandatory for every client, not just public ones. `state` still
 *     defends against CSRF on the redirect; `code_verifier` defends against
 *     interception of the code itself. Different problems, so we send both.
 *   - The implicit and password grants are gone. There is no supported way to
 *     trade a username and password for a token, so never ask a customer for
 *     their brokerage credentials.
 *   - Refresh tokens rotate. Each refresh returns a new one and kills the old.
 *     Persist it atomically -- see TokenStore.save.
 *   - Replaying a rotated refresh token is treated as theft and revokes the
 *     whole family. That bounds the damage from a stolen token to one use.
 *
 * Run directly for an interactive authorization:
 *
 *     node pkce-auth.mjs
 */

import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';
import { chmod, readFile, rename, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';

export const AUTH_BASE = process.env.BROKERAGE_AUTH_BASE ?? 'https://auth.brokerage.example.com';
export const CLIENT_ID = process.env.BROKERAGE_CLIENT_ID ?? 'your-client-id';
export const REDIRECT_URI = process.env.BROKERAGE_REDIRECT_URI ?? 'http://127.0.0.1:8723/callback';

export const SCOPES = [
  'accounts:read',
  'positions:read',
  'orders:read',
  'orders:write',
  'transactions:read',
  'instruments:read',
  'offline_access',
];

export class OAuthError extends Error {
  constructor(code, description) {
    super(`${code}: ${description}`);
    this.name = 'OAuthError';
    this.code = code;
    this.description = description;
  }
}

// ---------------------------------------------------------------------------
// PKCE
// ---------------------------------------------------------------------------

/** Base64url without padding, per RFC 7636. */
const b64url = (buf) => buf.toString('base64url');

/**
 * Generate a PKCE verifier/challenge pair using S256.
 *
 * 32 random bytes yields a 43-character verifier, the shortest RFC 7636
 * allows. `randomBytes`, never `Math.random`: the verifier is the only thing
 * stopping someone who intercepts the authorization code from redeeming it.
 *
 * The `plain` method exists in the RFC but this authorization server rejects
 * it, and it should not be used anywhere.
 */
export function generatePkcePair() {
  const codeVerifier = b64url(randomBytes(32));
  const codeChallenge = b64url(createHash('sha256').update(codeVerifier).digest());
  return { codeVerifier, codeChallenge };
}

export function buildAuthorizationUrl(codeChallenge, state) {
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: CLIENT_ID,
    redirect_uri: REDIRECT_URI,
    scope: SCOPES.join(' '),
    state,
    code_challenge: codeChallenge,
    code_challenge_method: 'S256',
  });
  return `${AUTH_BASE}/oauth2/authorize?${params}`;
}

// ---------------------------------------------------------------------------
// Tokens
// ---------------------------------------------------------------------------

export class Tokens {
  constructor({ accessToken, refreshToken, expiresAt, scope, tokenType = 'Bearer' }) {
    this.accessToken = accessToken;
    this.refreshToken = refreshToken ?? null;
    this.expiresAt = expiresAt;              // absolute epoch ms, not a duration
    this.scope = scope ?? '';
    this.tokenType = tokenType;
  }

  /**
   * Refresh 60s early. A token that passes this check locally can still be
   * rejected -- clocks drift and tokens can be revoked at any moment -- so the
   * client must also handle a 401 on any request.
   */
  get expired() {
    return Date.now() >= this.expiresAt - 60_000;
  }

  static fromResponse(payload, previous = null) {
    return new Tokens({
      accessToken: payload.access_token,
      // A refresh response omitting refresh_token means the old one is still
      // current. Keep it rather than dropping to null.
      refreshToken: payload.refresh_token ?? previous?.refreshToken ?? null,
      expiresAt: Date.now() + (payload.expires_in ?? 900) * 1000,
      scope: payload.scope,
      tokenType: payload.token_type,
    });
  }
}

/**
 * File-backed token storage.
 *
 * Two details that matter more than they look:
 *
 *   - Mode 0600. A refresh token is a 90-day bearer credential for someone's
 *     brokerage account; it must not be world-readable.
 *   - Atomic rename. Rotation kills the old refresh token the instant the new
 *     one is issued, so a partial write during rotation leaves the user with
 *     nothing usable.
 *
 * For anything multi-user, use a real secret manager instead.
 */
export class TokenStore {
  constructor(path = '.brokerage-tokens.json') {
    this.path = path;
  }

  async load() {
    try {
      return new Tokens(JSON.parse(await readFile(this.path, 'utf8')));
    } catch {
      return null;
    }
  }

  async save(tokens) {
    const tmp = `${this.path}.${process.pid}.tmp`;
    await writeFile(tmp, JSON.stringify(tokens), { mode: 0o600 });
    await chmod(tmp, 0o600);
    await rename(tmp, this.path);
  }
}

// ---------------------------------------------------------------------------
// Token endpoint
// ---------------------------------------------------------------------------

async function postForm(url, form) {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Accept: 'application/json',
    },
    body: new URLSearchParams(form),
  });

  const text = await response.text();
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new OAuthError('http_error', `${response.status}: ${text.slice(0, 400)}`);
  }

  if (!response.ok) {
    throw new OAuthError(payload.error ?? 'unknown_error', payload.error_description ?? text);
  }
  return payload;
}

/**
 * Trade an authorization code for tokens.
 *
 * Note the absence of a client_secret. This is a public client, so PKCE does
 * the work a secret would have done -- and a secret shipped in a browser
 * bundle or a mobile binary only creates the illusion of confidentiality. A
 * confidential server-side client would add `private_key_jwt` here instead.
 */
export async function exchangeCode(code, codeVerifier) {
  return Tokens.fromResponse(
    await postForm(`${AUTH_BASE}/oauth2/token`, {
      grant_type: 'authorization_code',
      code,
      redirect_uri: REDIRECT_URI,
      client_id: CLIENT_ID,
      code_verifier: codeVerifier,
    }),
  );
}

/**
 * Exchange a refresh token for a fresh pair.
 *
 * The returned refresh token replaces the one you sent. Save it before using
 * the new access token, so a crash between the two leaves you holding the
 * token that still works.
 */
export async function refreshTokens(tokens) {
  if (!tokens.refreshToken) {
    throw new OAuthError('no_refresh_token', 'Request the offline_access scope to receive one.');
  }
  return Tokens.fromResponse(
    await postForm(`${AUTH_BASE}/oauth2/token`, {
      grant_type: 'refresh_token',
      refresh_token: tokens.refreshToken,
      client_id: CLIENT_ID,
    }),
    tokens,
  );
}

/**
 * Revoke a token (RFC 7009). Call this on logout -- dropping tokens on the
 * floor leaves a live account credential in whatever logs captured it.
 */
export async function revoke(token, tokenTypeHint = 'refresh_token') {
  await postForm(`${AUTH_BASE}/oauth2/revoke`, {
    token,
    token_type_hint: tokenTypeHint,
    client_id: CLIENT_ID,
  });
}

// ---------------------------------------------------------------------------
// Interactive authorization
// ---------------------------------------------------------------------------

function openBrowser(url) {
  const command =
    process.platform === 'darwin' ? 'open' : process.platform === 'win32' ? 'start' : 'xdg-open';
  spawn(command, [url], { stdio: 'ignore', detached: true, shell: process.platform === 'win32' })
    .on('error', () => {})
    .unref();
}

/** Constant-time string compare, for `state`. */
function safeEqual(a = '', b = '') {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  return bufA.length === bufB.length && timingSafeEqual(bufA, bufB);
}

/**
 * Run the full browser-based flow and resolve to Tokens.
 *
 * Suitable for a CLI or desktop tool. A web application would instead redirect
 * the user's browser and handle the callback in a normal route, keeping
 * `state` and `codeVerifier` in the session.
 */
export function authorizeInteractively({ timeoutMs = 300_000 } = {}) {
  const { codeVerifier, codeChallenge } = generatePkcePair();
  const state = b64url(randomBytes(16));
  const redirect = new URL(REDIRECT_URI);

  return new Promise((resolve, reject) => {
    const server = createServer(async (req, res) => {
      const url = new URL(req.url, `http://${req.headers.host}`);
      if (url.pathname !== redirect.pathname) {
        res.writeHead(404).end();
        return;
      }

      const code = url.searchParams.get('code');
      const error = url.searchParams.get('error');
      const returnedState = url.searchParams.get('state');

      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(
        `<html><body><p>${
          code ? 'Authorization complete. You can close this tab.' : `Authorization failed: ${error}`
        }</p></body></html>`,
      );

      clearTimeout(timer);
      server.close();

      if (error) {
        reject(new OAuthError(error, url.searchParams.get('error_description') ?? ''));
        return;
      }
      if (!safeEqual(returnedState, state)) {
        reject(new OAuthError('state_mismatch', 'Possible CSRF; discarding the code.'));
        return;
      }
      try {
        resolve(await exchangeCode(code, codeVerifier));
      } catch (err) {
        reject(err);
      }
    });

    const timer = setTimeout(() => {
      server.close();
      reject(new OAuthError('timeout', `No callback within ${timeoutMs / 1000}s.`));
    }, timeoutMs);

    server.listen(Number(redirect.port), redirect.hostname, () => {
      const authUrl = buildAuthorizationUrl(codeChallenge, state);
      console.log('Opening your browser to authorize...');
      console.log(`  If it does not open, visit:\n  ${authUrl}\n`);
      openBrowser(authUrl);
    });
  });
}

/**
 * Return usable tokens, refreshing or re-authorizing as needed.
 *
 * This is what application code should call. It encapsulates the three-way
 * branch every OAuth client needs: no tokens, stale tokens, good tokens.
 */
export async function getValidTokens(store = new TokenStore()) {
  let tokens = await store.load();

  if (!tokens) {
    tokens = await authorizeInteractively();
    await store.save(tokens);
    return tokens;
  }

  if (tokens.expired) {
    try {
      tokens = await refreshTokens(tokens);
    } catch (err) {
      // invalid_grant means revoked, expired, or already used. The only
      // recovery is sending the user back through authorization.
      if (!(err instanceof OAuthError) || err.code !== 'invalid_grant') throw err;
      console.log(`Refresh failed (${err.description}); re-authorizing.`);
      tokens = await authorizeInteractively();
    }
    await store.save(tokens);
  }

  return tokens;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const tokens = await getValidTokens();
  console.log('Access token acquired.');
  console.log(`  scope   : ${tokens.scope}`);
  console.log(`  expires : ${new Date(tokens.expiresAt).toISOString()}`);
  console.log(`  refresh : ${tokens.refreshToken ? 'yes' : 'no (offline_access not granted)'}`);
}
