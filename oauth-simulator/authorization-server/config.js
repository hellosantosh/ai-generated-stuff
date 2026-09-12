'use strict';
/** Static configuration: who can log in, which apps exist, what they may ask for. */

const env = process.env;

/**
 * The *issuer* is the public, browser-visible identity of this authorization
 * server. Every token it mints carries it as `iss`, and every resource server
 * checks it. Inside the Docker network the containers reach each other by
 * service name, but the issuer must stay the URL the browser uses -- mixing
 * the two up is the single most common local-development OAuth bug.
 */
const ISSUER = env.ISSUER || 'http://localhost:9000';
const PORT = Number(env.PORT || 9000);

/** Human beings. The IdP half of this service authenticates these. */
const USERS = [
  {
    sub: 'u-1001',
    username: 'alice',
    password: 'wonderland',
    name: 'Alice Liddell',
    email: 'alice@simulator.test',
    email_verified: true,
    roles: ['customer'],
    // What this *user* is allowed to delegate. A client can never receive a
    // scope the user does not hold, no matter what it asks for.
    entitlements: ['openid', 'profile', 'email', 'accounts:read', 'payments:write'],
    accounts: [
      { id: 'ACC-4410', label: 'Everyday Checking', balance: 4820.55, currency: 'USD' },
      { id: 'ACC-9982', label: 'Rainy Day Savings', balance: 15200.0, currency: 'USD' },
    ],
  },
  {
    sub: 'u-1002',
    username: 'bob',
    password: 'builder',
    name: 'Bob Builder',
    email: 'bob@simulator.test',
    email_verified: true,
    roles: ['customer'],
    // Bob deliberately lacks `payments:write` so you can watch a scope get
    // refused at consent time rather than at the API.
    entitlements: ['openid', 'profile', 'email', 'accounts:read'],
    accounts: [{ id: 'ACC-7100', label: 'Joint Account', balance: 310.2, currency: 'USD' }],
  },
];

/** Every scope this AS knows about, with consent-screen wording. */
const SCOPES = {
  openid: { label: 'Sign you in', description: 'Confirm who you are and issue an ID token (this is OpenID Connect).' },
  profile: { label: 'Read your basic profile', description: 'Your display name.' },
  email: { label: 'Read your email address', description: 'Your email address and whether it is verified.' },
  'accounts:read': { label: 'View your accounts', description: 'List your account names and balances. Read-only.' },
  'payments:write': { label: 'Move money', description: 'Initiate payments from your accounts. Powerful -- grant carefully.' },
};

/**
 * The client registry. In production this lives in a database and is managed
 * by dynamic registration (RFC 7591) or an admin console.
 */
const CLIENTS = [
  {
    client_id: 'demo-web-app',
    client_name: 'Demo Web App (confidential)',
    // A server-side web app can keep a secret, so it authenticates to the
    // token endpoint. Public clients (SPA, mobile) cannot and must not.
    client_secret: env.DEMO_WEB_APP_SECRET || 'web-app-super-secret',
    token_endpoint_auth_method: 'client_secret_basic',
    client_type: 'confidential',
    redirect_uris: ['http://localhost:9020/callback'],
    post_logout_redirect_uris: ['http://localhost:9020/'],
    grant_types: ['authorization_code', 'refresh_token'],
    allowed_scopes: ['openid', 'profile', 'email', 'accounts:read', 'payments:write'],
    require_pkce: true,
    audience: ['http://localhost:9010'],
  },
  {
    client_id: 'demo-spa',
    client_name: 'Demo SPA (public)',
    // No secret: anyone can download the JavaScript and read it. PKCE is the
    // only thing binding the authorization code to this app.
    client_secret: null,
    token_endpoint_auth_method: 'none',
    client_type: 'public',
    redirect_uris: ['http://localhost:9020/spa/callback'],
    grant_types: ['authorization_code', 'refresh_token'],
    allowed_scopes: ['openid', 'profile', 'accounts:read'],
    require_pkce: true,
    audience: ['http://localhost:9010'],
  },
  {
    client_id: 'demo-service',
    client_name: 'Demo Batch Service (machine-to-machine)',
    client_secret: env.DEMO_SERVICE_SECRET || 'service-super-secret',
    token_endpoint_auth_method: 'client_secret_basic',
    client_type: 'confidential',
    redirect_uris: [],
    // No user is involved, so there is nothing to redirect and no refresh
    // token: the client just asks again with its own credentials.
    grant_types: ['client_credentials'],
    allowed_scopes: ['accounts:read'],
    require_pkce: false,
    audience: ['http://localhost:9010'],
  },
  {
    client_id: 'evil-app',
    client_name: 'Totally Legit Coupon Finder (the attacker)',
    client_secret: null,
    token_endpoint_auth_method: 'none',
    client_type: 'public',
    // Registered so Lab 02 can show a *stolen code* being replayed by a
    // different app. With PKCE on, the theft is worthless.
    redirect_uris: ['http://localhost:9020/labs/evil/callback'],
    grant_types: ['authorization_code'],
    allowed_scopes: ['openid', 'profile', 'accounts:read'],
    require_pkce: true,
    audience: ['http://localhost:9010'],
  },
];

/**
 * Policy switches. Every one of these is ON in a correct OAuth 2.1 server.
 * Turning one OFF is how the labs demonstrate what the rule was protecting
 * you from -- the AS shouts a warning in the trace whenever it runs unsafely.
 */
const DEFAULT_POLICY = {
  require_pkce: true,                  // RFC 9700 / OAuth 2.1: PKCE for every authorization_code request
  allow_plain_code_challenge: false,   // S256 only; `plain` is pointless over an insecure channel
  require_exact_redirect_uri: true,    // OAuth 2.1: string equality, no wildcards, no prefix matching
  require_state: false,                // With PKCE, state is for app-level CSRF + round-tripping UI state
  single_use_codes: true,              // Replaying a code must fail and revoke the tokens it minted
  rotate_refresh_tokens: true,         // Public clients must get a new refresh token each time
  detect_refresh_reuse: true,          // A replayed refresh token kills the whole family (RFC 9700 §4.14)
  enforce_audience: true,              // Tokens name the API they are for
  allow_legacy_grants: false,          // OAuth 2.1 removed implicit and password grants
};

const LIFETIMES = {
  authorization_code_sec: Number(env.CODE_TTL_SEC || 60),   // short: it crosses the browser
  access_token_sec: Number(env.ACCESS_TOKEN_TTL_SEC || 300), // short: it cannot be revoked mid-life
  refresh_token_sec: Number(env.REFRESH_TOKEN_TTL_SEC || 3600),
  id_token_sec: Number(env.ID_TOKEN_TTL_SEC || 300),
  session_sec: Number(env.SESSION_TTL_SEC || 1800),
};

const findClient = (clientId) => CLIENTS.find((c) => c.client_id === clientId) || null;
const findUser = (username) => USERS.find((u) => u.username === String(username || '').toLowerCase().trim()) || null;
const findUserBySub = (sub) => USERS.find((u) => u.sub === sub) || null;

module.exports = { ISSUER, PORT, USERS, CLIENTS, SCOPES, DEFAULT_POLICY, LIFETIMES, findClient, findUser, findUserBySub };
