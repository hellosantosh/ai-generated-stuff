# OAuth 2.1 + PKCE Simulator

A complete, working OAuth 2.1 deployment that runs on your laptop and is built to be
taken apart: an **identity provider**, an **authorization server**, a **resource server**
(API), and a **client application** with 13 hands-on labs.

Everything is implemented on **Node core modules with zero npm dependencies**, so every
byte of the protocol is in this repository rather than hidden inside a library. No
`npm install`, no lockfile, no framework magic — you can read the whole authorization
server top to bottom in one sitting.

```
┌──────────────────┐         browser redirects        ┌──────────────────────────┐
│   Client App     │ ───────────────────────────────► │  IdP + Authorization     │
│   :9020          │ ◄─────────────────────────────── │  Server        :9000     │
│  13 labs + flow  │      code (front channel)        │  login · consent ·       │
│    inspector     │                                  │  tokens · policy · trace │
└────────┬─────────┘                                  └────────────┬─────────────┘
         │                                                         │
         │ POST /token  code + code_verifier                       │ publishes
         │ (back channel — the browser never sees this)            │ JWKS
         │                                                         ▼
         │            ┌──────────────────────────────────────────────────┐
         └──────────► │  Resource Server (API)              :9010        │
           Bearer     │  verifies signature · iss · aud · exp · scope    │
            token     └──────────────────────────────────────────────────┘
```

**New to OAuth?** Read [`dev-guide/dev-guide.pdf`](dev-guide/dev-guide.pdf) first — a
20-page illustrated developer guide that explains OAuth 2.1 and PKCE from first
principles, with full-page diagrams. Then come back and run the labs.

---

## Quick start

### Start

```bash
cd oauth-simulator
make up            # builds the images and starts all three services
```

Then open **<http://localhost:9020>** and sign in as **`alice` / `wonderland`**.

`make up` waits until all three services report healthy, then prints the URLs.
First build takes ~20 seconds; there are no packages to download.

### Stop

```bash
make down          # stop and remove the containers
```

### Run (the labs)

Open <http://localhost:9020>, pick **Lab 01**, and press *Run Lab 01*. You will be
redirected to the identity provider, sign in, approve the consent screen, and land back
in the client's **flow inspector** — which shows every request, every parameter, and an
explanation of what each step proved.

Then run **Lab 02**, which hands your authorization code to an attacker and shows PKCE
making it worthless. Then **Lab 03**, which turns PKCE off and shows the same theft
succeeding.

### No Docker?

```bash
make local         # runs the three services with plain Node (18+)
make local-stop
```

Identical behaviour — the services have no dependencies to install.

---

## Every command

| Command | What it does |
| --- | --- |
| `make up` | Build and start everything in Docker, wait for health, print URLs |
| `make down` | Stop and remove the containers |
| `make restart` | Restart all services (**wipes state and rotates signing keys**) |
| `make rebuild` | Rebuild images from scratch, then start |
| `make logs` | Follow all logs — this is where you watch the protocol run |
| `make logs-as` | Follow just the authorization server |
| `make ps` | Container and health status |
| `make local` | Start without Docker, using plain Node |
| `make local-stop` / `local-restart` / `local-status` / `local-logs` | Same, without Docker |
| `make demo` | Walk the whole flow on the command line with curl |
| `make demo-attack` | Command-line demo: stolen code, blocked by PKCE |
| `make demo-replay` | Command-line demo: replaying a used code |
| `make demo-refresh` | Command-line demo: refresh rotation + reuse detection |
| `make test` | Run all four command-line scenarios as a smoke test |
| `make reset` | Wipe sessions, codes, tokens and consent; restore safe policy |
| `make open` | Open the simulator in your browser |
| `make policy` | Open the page where you break OAuth rules on purpose |
| `make guide` | Rebuild `dev-guide/dev-guide.pdf` from its HTML source |
| `make clean` | Stop everything and remove run artifacts |
| `make help` | List all targets |

---

## What is running

| Service | URL | Role |
| --- | --- | --- |
| **Client App** | <http://localhost:9020> | The application that wants your data. Holds the labs and the flow inspector. |
| **IdP + Authorization Server** | <http://localhost:9000> | Authenticates you, asks your permission, issues and validates tokens. |
| **Resource Server** | <http://localhost:9010> | The API holding the data. Trusts tokens, not sessions. |

### Test accounts

| Username | Password | Can delegate |
| --- | --- | --- |
| `alice` | `wonderland` | Everything, including `payments:write` |
| `bob` | `builder` | Read-only — deliberately **cannot** delegate `payments:write` |

### Registered clients

| `client_id` | Type | Secret | Grants |
| --- | --- | --- | --- |
| `demo-web-app` | confidential | `web-app-super-secret` | `authorization_code`, `refresh_token` |
| `demo-spa` | **public** (no secret) | — | `authorization_code`, `refresh_token` |
| `demo-service` | confidential | `service-super-secret` | `client_credentials` |
| `evil-app` | public | — | used by the attack labs |

---

## The 13 labs

| # | Lab | What it proves |
| --- | --- | --- |
| 01 | Authorization Code + PKCE | The one flow OAuth 2.1 wants. Everything else is a variation. |
| 02 | The stolen authorization code | A leaked code is worthless without the `code_verifier`. |
| 03 | OAuth 2.0 without PKCE | Turn PKCE off and the same theft succeeds. **Run 02 and 03 back to back.** |
| 04 | The wrong `code_verifier` | `invalid_grant`, and the code is burned. |
| 05 | A public client (SPA / mobile) | No secret anywhere; PKCE is the only proof of identity. |
| 06 | Refresh tokens and rotation | Replaying a retired refresh token revokes the whole family. |
| 07 | Scopes, consent, least privilege | Scope is capped by registration, entitlement **and** consent. |
| 08 | ID token is not an access token | The most common OAuth bug in production, demonstrated twice. |
| 09 | `redirect_uri` exact matching | Why wildcards hand your codes to an attacker. |
| 10 | `state`, CSRF and mix-up | What `state` is actually for now that PKCE exists. |
| 11 | Machine to machine | `client_credentials`: no user, no consent, no refresh token. |
| 12 | Introspection vs local validation | Revoke a token, then watch one API notice and the other not. |
| 13 | What OAuth 2.1 deleted | The implicit and password grants, and why losing them is a win. |

---

## Break it on purpose

The most valuable page in the simulator is **<http://localhost:9000/policy>**. Every
switch there is a requirement of OAuth 2.1 or RFC 9700, and every one can be turned off
at runtime — no restart:

| Switch | Turn it off to see |
| --- | --- |
| `require_pkce` | A stolen authorization code become a working access token |
| `allow_plain_code_challenge` | Why `plain` is not a hash and protects nothing |
| `require_exact_redirect_uri` | Prefix matching deliver a code to an attacker's path |
| `single_use_codes` | A replayed code mint fresh tokens forever |
| `rotate_refresh_tokens` | One long-lived refresh token used over and over |
| `detect_refresh_reuse` | A stolen refresh token work indefinitely, silently |
| `enforce_audience` | A token for one API accepted by another |
| `allow_legacy_grants` | The implicit and password grants that OAuth 2.1 removed |

The authorization server logs a **warning on every unsafe path it takes**, so you can
always see *why* something succeeded that should not have. `make reset` restores safe
defaults.

---

## Watch the protocol run

Three views, best kept open side by side:

- **<http://localhost:9000/trace>** — live stream of every decision the authorization
  server makes: which rule it checked, what it compared, why the request passed or failed.
- **<http://localhost:9000/state>** — everything the server currently remembers: sessions,
  codes and the PKCE challenge each is bound to, refresh-token families, stored consent.
- **<http://localhost:9020/flow>** — the client's side: every request it made, with the
  exact parameters and a `curl` equivalent you can paste into a terminal.

Or just `make logs`.

---

## Prefer a terminal?

```bash
make demo          # the full flow, one curl at a time, every parameter printed
make demo-attack   # a stolen code, refused by PKCE
make test          # all four scenarios as a smoke test
```

These need no browser. `scripts/demo-flow.sh` is also the shortest readable description
of the protocol in this repository — about 200 lines of `curl` and `openssl`.

Compute a PKCE pair by hand:

```bash
verifier=$(openssl rand 32 | openssl base64 -A | tr '/+' '_-' | tr -d '=')
challenge=$(printf '%s' "$verifier" | openssl dgst -binary -sha256 \
            | openssl base64 -A | tr '/+' '_-' | tr -d '=')
echo "verifier:  $verifier"
echo "challenge: $challenge"

# and check your arithmetic against the authorization server:
curl -s "http://localhost:9000/pkce/explain?verifier=$verifier"
```

---

## What is implemented

| Specification | Coverage |
| --- | --- |
| OAuth 2.1 (draft) | Authorization code with mandatory PKCE, refresh, client credentials; implicit and password grants removed; exact redirect-URI matching; no bearer tokens in query strings |
| RFC 7636 | PKCE, `S256` (and `plain`, refused unless you enable it to see why) |
| RFC 6749 / 6750 | Core grants and Bearer token usage, as narrowed by 2.1 |
| RFC 8414 | Authorization server metadata at `/.well-known/oauth-authorization-server` |
| RFC 7662 | Token introspection, with client authentication required |
| RFC 7009 | Token revocation, including whole refresh-token families |
| RFC 9068 | JWT access tokens (`typ: at+jwt`) |
| RFC 8707 | Resource indicators (the `resource` parameter sets `aud`) |
| RFC 9207 | `iss` in the authorization response (mix-up defence) |
| RFC 9700 | Security BCP: rotation, reuse detection, code replay handling |
| RFC 7638 | JWK thumbprint as `kid`, so key rotation is detectable |
| OpenID Connect Core | `openid` scope, ID tokens, `nonce`, `/userinfo` (a working subset) |

### Endpoints

**Authorization server** — `/.well-known/oauth-authorization-server`,
`/.well-known/openid-configuration`, `/authorize`, `/token`, `/userinfo`, `/introspect`,
`/revoke`, `/jwks.json`, `/logout`, plus the simulator's own `/policy`, `/state`,
`/trace` and `/pkce/explain`.

**Resource server** — `GET /api/me` (`profile`), `GET /api/accounts` (`accounts:read`),
`POST /api/payments` (`payments:write`), `GET /api/accounts-introspected`, and
`POST /debug/token`, which explains every check it runs against a token you paste in.

---

## Layout

```
oauth-simulator/
├── README.md
├── Makefile                  every command above
├── docker-compose.yml        three services, healthchecks, front/back channel URLs
├── shared/                   no framework, just these five files
│   ├── http.js               tiny router, cookies, form parsing, SSE
│   ├── jose.js               RS256 JWS, JWKS, and the PKCE primitives
│   ├── diagram.js            the SVG sequence diagrams
│   ├── trace.js              the live protocol trace bus
│   └── ui.js                 shared CSS and HTML helpers
├── authorization-server/     the IdP + AS: config, state store, views, server
├── resource-server/          the API, and the four checks it runs per request
├── client-app/               the labs, the flow engine, the inspector
├── scripts/
│   ├── demo-flow.sh          the whole flow in curl
│   └── run-local.sh          start/stop without Docker
└── dev-guide/
    ├── dev-guide.pdf         the illustrated developer guide, 22 pages  ← start here
    ├── build-guide.sh        regenerates the PDF (needs Chrome or Chromium)
    ├── renumber.py           derives page numbers, so the contents never drifts
    └── src/dev-guide.html    the source: fixed-layout HTML with inline SVG diagrams
```

---

## Notes and limitations

This is a **teaching simulator**, not a product. Deliberately:

- **Everything is in memory.** Restarting a service resets it — that is the reset button.
- **Signing keys are generated at boot**, so restarting the authorization server *rotates*
  its keys. The `kid` is a JWK thumbprint (RFC 7638), so the resource server notices the
  new key and refetches the JWKS automatically. Watch it happen in `make logs`.
- **Everything is HTTP, not HTTPS.** Real OAuth requires TLS on every endpoint; `localhost`
  is the one exception the specs allow.
- **Passwords are in plain text** in `authorization-server/config.js` so you can read them.
  Real deployments store a slow password hash (Argon2id, scrypt, bcrypt).
- **Consent is always shown**, even when it was already granted, so you can see the screen.
- The `evil-app` client and the policy switches exist so attacks can be demonstrated
  *against your own machine*. Do not point the attack labs at anything you do not own.

