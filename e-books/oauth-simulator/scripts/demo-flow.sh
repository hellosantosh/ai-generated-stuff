#!/usr/bin/env bash
#
# The whole Authorization Code + PKCE flow on the command line, one curl at a
# time, with every parameter printed. No browser, no framework, no magic.
#
#   ./scripts/demo-flow.sh                 happy path
#   ./scripts/demo-flow.sh --attack        redeem a stolen code without the verifier
#   ./scripts/demo-flow.sh --replay        redeem the same code twice
#   ./scripts/demo-flow.sh --refresh       rotate a refresh token, then reuse the old one
#   ./scripts/demo-flow.sh --all           run every scenario
#
set -euo pipefail

AS="${AS_URL:-http://localhost:9000}"
RS="${RS_URL:-http://localhost:9010}"
CLIENT_ID="${CLIENT_ID:-demo-web-app}"
CLIENT_SECRET="${CLIENT_SECRET:-web-app-super-secret}"
REDIRECT_URI="${REDIRECT_URI:-http://localhost:9020/callback}"
SCOPE="${SCOPE:-openid profile accounts:read}"
USERNAME="${USERNAME:-alice}"
PASSWORD="${PASSWORD:-wonderland}"

B="\033[1m"; D="\033[2m"; R="\033[0m"
CY="\033[36m"; GR="\033[32m"; YE="\033[33m"; RE="\033[31m"; MA="\033[35m"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
JAR="$TMP/cookies.txt"

step()  { printf "\n${CY}${B}=== %s ===${R}\n" "$*"; }
say()   { printf "  %s\n" "$*"; }
why()   { printf "  ${D}%s${R}\n" "$*"; }
val()   { printf "  ${MA}%-22s${R} %s\n" "$1" "$2"; }
good()  { printf "  ${GR}${B}PASS${R} %s\n" "$*"; }
bad()   { printf "  ${RE}${B}FAIL${R} %s\n" "$*"; }
warn()  { printf "  ${YE}${B}NOTE${R} %s\n" "$*"; }

b64url() { openssl base64 -A | tr '/+' '_-' | tr -d '='; }

# ---------------------------------------------------------------- PKCE
make_pkce() {
  VERIFIER="$(openssl rand 32 | b64url)"
  CHALLENGE="$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | b64url)"
}

# Walk the front channel with a cookie jar standing in for a browser.
# Prints the authorization code on stdout.
get_code() {
  local challenge="$1" extra="${2:-}"
  local authorize_url="$AS/authorize?response_type=code&client_id=$CLIENT_ID&redirect_uri=$(urlenc "$REDIRECT_URI")&scope=$(urlenc "$SCOPE")&state=$STATE&nonce=$NONCE$extra"
  [ -n "$challenge" ] && authorize_url="$authorize_url&code_challenge=$challenge&code_challenge_method=S256"

  local login_html request_id location
  login_html="$(curl -s -c "$JAR" -b "$JAR" "$authorize_url")"
  request_id="$(printf '%s' "$login_html" | grep -o 'name="request_id" value="[^"]*"' | head -1 | sed 's/.*value="//;s/"//')"
  if [ -z "$request_id" ]; then
    printf '%s' "$login_html" | grep -o 'pill err">[^<]*<' | sed 's/pill err">//;s/<//' >&2
    echo "ERROR_NO_REQUEST_ID" ; return 0
  fi

  # POST the password to the IdP -- note this happens on the IdP's own origin.
  location="$(curl -s -c "$JAR" -b "$JAR" -o /dev/null -D - -X POST "$AS/login" \
    --data-urlencode "request_id=$request_id" \
    --data-urlencode "username=$USERNAME" \
    --data-urlencode "password=$PASSWORD" | awk '/^[Ll]ocation:/{print $2}' | tr -d '\r')"

  # Follow to the consent screen, then approve every requested scope.
  curl -s -c "$JAR" -b "$JAR" "$AS$location" > "$TMP/consent.html"
  local consent_args=(--data-urlencode "request_id=$request_id" --data-urlencode "action=allow")
  while read -r s; do [ -n "$s" ] && consent_args+=(--data-urlencode "scope=$s"); done <<< "$(echo "$SCOPE" | tr ' ' '\n')"

  location="$(curl -s -c "$JAR" -b "$JAR" -o /dev/null -D - -X POST "$AS/consent" "${consent_args[@]}" \
    | awk '/^[Ll]ocation:/{print $2}' | tr -d '\r')"
  echo "$location"
}

urlenc() { python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$1"; }
qparam() { python3 -c "
import sys,urllib.parse as u
q=u.parse_qs(u.urlparse(sys.argv[1]).query)
print(q.get(sys.argv[2],[''])[0])" "$1" "$2"; }
jfield() { python3 -c "
import sys,json
try: print(json.load(sys.stdin).get(sys.argv[1],''))
except Exception: print('')" "$1"; }

decode_jwt() {
  python3 - "$1" <<'PY'
import base64,json,sys
def seg(s): return json.loads(base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)))
p = sys.argv[1].split('.')
print("  header  ", json.dumps(seg(p[0])))
print("  payload ", json.dumps(seg(p[1]), indent=2).replace("\n", "\n          "))
PY
}

exchange_code() {
  local code="$1" verifier="$2"
  local args=(--data-urlencode "grant_type=authorization_code"
              --data-urlencode "code=$code"
              --data-urlencode "redirect_uri=$REDIRECT_URI")
  [ -n "$verifier" ] && args+=(--data-urlencode "code_verifier=$verifier")
  curl -s -u "$CLIENT_ID:$CLIENT_SECRET" -X POST "$AS/token" "${args[@]}"
}

new_flow() { STATE="$(openssl rand 8 | b64url)"; NONCE="$(openssl rand 8 | b64url)"; }

# ================================================================ happy path
scenario_happy() {
  printf "\n${B}################  SCENARIO: the correct flow  ################${R}\n"
  new_flow; make_pkce

  step "1. The client invents a secret it never sends: the code_verifier"
  val "code_verifier" "$VERIFIER"
  why "43 random base64url characters. It stays in the client's memory/session."
  val "code_challenge" "$CHALLENGE"
  why "= BASE64URL(SHA256(code_verifier)). This one-way hash is safe to send"
  why "through the browser, because it cannot be reversed into the verifier."

  step "2. Front channel: GET /authorize (browser + cookie jar)"
  why "The user authenticates at the IdP and consents. Nothing secret travels here."
  local location code returned_state returned_iss
  location="$(get_code "$CHALLENGE")"
  code="$(qparam "$location" code)"
  returned_state="$(qparam "$location" state)"
  returned_iss="$(qparam "$location" iss)"
  val "redirected to" "$(echo "$location" | cut -c1-46)..."
  val "code" "$code"
  val "state echoed" "$returned_state"
  val "iss (RFC 9207)" "$returned_iss"
  [ "$returned_state" = "$STATE" ] && good "state matches what we sent -- this callback is ours" || bad "state mismatch"

  step "3. Back channel: POST /token with code + code_verifier"
  why "Server-to-server. The AS hashes our verifier and compares it to the"
  why "challenge it stored in step 2. Only the app that made the verifier passes."
  local resp access_token refresh_token
  resp="$(exchange_code "$code" "$VERIFIER")"
  access_token="$(echo "$resp" | jfield access_token)"
  refresh_token="$(echo "$resp" | jfield refresh_token)"
  if [ -n "$access_token" ]; then
    good "tokens issued"
    val "token_type" "$(echo "$resp" | jfield token_type)"
    val "expires_in" "$(echo "$resp" | jfield expires_in)s"
    val "scope" "$(echo "$resp" | jfield scope)"
    val "refresh_token" "${refresh_token:0:16}..."
  else
    bad "$(echo "$resp" | jfield error_description)"; return 1
  fi

  step "4. What is actually inside the access token"
  decode_jwt "$access_token"
  why "Note aud (which API may accept it), scope (what it may do) and exp."

  local id_token; id_token="$(echo "$resp" | jfield id_token)"
  if [ -n "$id_token" ]; then
    step "5. And the ID token, which is a different thing entirely"
    decode_jwt "$id_token"
    why "aud = the CLIENT, not the API. An ID token proves who signed in;"
    why "it is NOT a key to any API. Never send it as a Bearer token."
  fi

  step "6. Call the resource server with the access token"
  local api; api="$(curl -s -H "Authorization: Bearer $access_token" "$RS/api/accounts")"
  if echo "$api" | grep -q '"accounts"'; then
    good "GET $RS/api/accounts returned data"
    echo "$api" | python3 -m json.tool | sed 's/^/    /' | head -18
  else
    warn "resource server says: $(echo "$api" | jfield error_description)"
  fi

  step "7. Ask for something the token is not scoped for"
  local denied; denied="$(curl -s -o "$TMP/d.json" -w '%{http_code}' -X POST -H "Authorization: Bearer $access_token" "$RS/api/payments")"
  if [ "$denied" = "403" ]; then
    good "HTTP 403 insufficient_scope -- the API enforced scope, not just a valid signature"
    why "$(jfield error_description < "$TMP/d.json")"
  else
    bad "expected 403, got $denied"
  fi

  LAST_REFRESH="$refresh_token"
  LAST_CODE="$code"
  LAST_VERIFIER="$VERIFIER"
}

# ================================================================ attack
scenario_attack() {
  printf "\n${B}################  SCENARIO: stolen authorization code  ################${R}\n"
  new_flow; make_pkce
  step "An attacker has captured the code from the redirect"
  why "Assume the worst: malicious app on the device, a leaked log, a crafted"
  why "redirect, a compromised proxy. The attacker has the code itself."
  local location code
  location="$(get_code "$CHALLENGE")"
  code="$(qparam "$location" code)"
  val "stolen code" "$code"

  step "Attacker redeems it -- but has no code_verifier"
  local resp; resp="$(exchange_code "$code" "")"
  if [ "$(echo "$resp" | jfield error)" = "invalid_grant" ]; then
    good "REFUSED: $(echo "$resp" | jfield error)"
    why "$(echo "$resp" | jfield error_description)"
  else
    bad "the attack succeeded -- is require_pkce turned off at $AS/policy ?"
  fi

  step "Attacker guesses a verifier"
  local fake; fake="$(openssl rand 32 | b64url)"
  resp="$(exchange_code "$code" "$fake")"
  if [ "$(echo "$resp" | jfield error)" = "invalid_grant" ]; then
    good "REFUSED: the hash of a guessed verifier never matches the stored challenge"
  else
    bad "unexpected success"
  fi
  warn "This is the entire value of PKCE: the code alone is worthless."
}

# ================================================================ replay
scenario_replay() {
  printf "\n${B}################  SCENARIO: replaying a used code  ################${R}\n"
  new_flow; make_pkce
  local location code resp
  location="$(get_code "$CHALLENGE")"; code="$(qparam "$location" code)"
  step "First redemption (legitimate)"
  resp="$(exchange_code "$code" "$VERIFIER")"
  [ -n "$(echo "$resp" | jfield access_token)" ] && good "tokens issued" || bad "$(echo "$resp" | jfield error_description)"
  step "Second redemption of the same code, same verifier"
  resp="$(exchange_code "$code" "$VERIFIER")"
  if [ "$(echo "$resp" | jfield error)" = "invalid_grant" ]; then
    good "REFUSED: codes are single-use"
    why "$(echo "$resp" | jfield error_description)"
  else
    bad "replay accepted -- is single_use_codes off at $AS/policy ?"
  fi
}

# ================================================================ refresh
scenario_refresh() {
  printf "\n${B}################  SCENARIO: refresh rotation + reuse detection  ################${R}\n"
  new_flow; make_pkce
  local location code resp rt1 rt2
  location="$(get_code "$CHALLENGE")"; code="$(qparam "$location" code)"
  resp="$(exchange_code "$code" "$VERIFIER")"
  rt1="$(echo "$resp" | jfield refresh_token)"
  val "refresh_token (gen 1)" "${rt1:0:20}..."

  step "Refresh once"
  resp="$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" -X POST "$AS/token" \
    --data-urlencode "grant_type=refresh_token" --data-urlencode "refresh_token=$rt1")"
  rt2="$(echo "$resp" | jfield refresh_token)"
  if [ -n "$rt2" ] && [ "$rt1" != "$rt2" ]; then
    good "ROTATED: a brand-new refresh token came back"
    val "refresh_token (gen 2)" "${rt2:0:20}..."
    why "The old value is now retired. Each refresh is a fresh, single-use credential."
  else
    warn "no rotation happened -- check rotate_refresh_tokens at $AS/policy"
  fi

  step "Now replay the RETIRED generation-1 token, as a thief would"
  resp="$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" -X POST "$AS/token" \
    --data-urlencode "grant_type=refresh_token" --data-urlencode "refresh_token=$rt1")"
  if [ "$(echo "$resp" | jfield error)" = "invalid_grant" ]; then
    good "REFUSED and the whole family was revoked"
    why "$(echo "$resp" | jfield error_description)"
  else
    bad "reuse accepted -- check detect_refresh_reuse at $AS/policy"
  fi

  step "The legitimate generation-2 token is collateral damage"
  resp="$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" -X POST "$AS/token" \
    --data-urlencode "grant_type=refresh_token" --data-urlencode "refresh_token=$rt2")"
  if [ "$(echo "$resp" | jfield error)" = "invalid_grant" ]; then
    good "also refused -- the AS cannot tell thief from victim, so it revokes both"
    why "The user signs in again. That is the correct, safe outcome."
  else
    warn "generation 2 still works"
  fi
}

# ================================================================ main
curl -s --retry 20 --retry-delay 1 --retry-connrefused "$AS/healthz" > /dev/null || {
  echo "Cannot reach the authorization server at $AS. Run 'make up' first."; exit 1; }

printf "${B}OAuth 2.1 + PKCE command-line walkthrough${R}\n"
printf "${D}AS: %s   RS: %s   client: %s   user: %s${R}\n" "$AS" "$RS" "$CLIENT_ID" "$USERNAME"

case "${1:---happy}" in
  --happy)   scenario_happy ;;
  --attack)  scenario_attack ;;
  --replay)  scenario_replay ;;
  --refresh) scenario_refresh ;;
  --all)     scenario_happy; scenario_attack; scenario_replay; scenario_refresh ;;
  *) echo "usage: $0 [--happy|--attack|--replay|--refresh|--all]"; exit 2 ;;
esac

printf "\n${D}Watch the server side of everything above at %s/trace${R}\n\n" "$AS"
