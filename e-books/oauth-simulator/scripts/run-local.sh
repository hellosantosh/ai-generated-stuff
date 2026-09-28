#!/usr/bin/env bash
#
# Run the simulator directly on your machine, without Docker.
#
# The services have zero npm dependencies, so all this needs is Node 18+.
# Useful when the Docker daemon is not running, or when you want to edit a
# file and restart in under a second.
#
#   ./scripts/run-local.sh start | stop | restart | status | logs [service]
#
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"

B="\033[1m"; D="\033[2m"; R="\033[0m"; GR="\033[32m"; RE="\033[31m"; YE="\033[33m"

# name|port|entrypoint
SERVICES=(
  "authorization-server|9000|authorization-server/server.js"
  "resource-server|9010|resource-server/server.js"
  "client-app|9020|client-app/server.js"
)

export ISSUER="${ISSUER:-http://localhost:9000}"
export AUDIENCE="${AUDIENCE:-http://localhost:9010}"
export TRUSTED_ISSUER="$ISSUER"
export AS_INTERNAL_URL="$ISSUER"
export AS_PUBLIC_URL="$ISSUER"
export RS_INTERNAL_URL="$AUDIENCE"
export SELF_URL="${SELF_URL:-http://localhost:9020}"

pid_file() { echo "$RUN_DIR/$1.pid"; }
log_file() { echo "$RUN_DIR/$1.log"; }

is_running() {
  local pf; pf="$(pid_file "$1")"
  [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null
}

# `|| true` matters: with `set -o pipefail`, an empty lsof result would
# otherwise abort the whole script.
port_owner() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }

start() {
  command -v node >/dev/null || { echo "Node.js is required (18+)."; exit 1; }
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name port script <<< "$entry"
    if is_running "$name"; then
      printf "  ${YE}already running${R} %-22s pid %s\n" "$name" "$(cat "$(pid_file "$name")")"
      continue
    fi
    owner="$(port_owner "$port")"
    if [ -n "$owner" ]; then
      printf "  ${RE}port %s is taken${R} by pid %s (%s)\n" "$port" "$owner" "$(ps -p "$owner" -o comm= 2>/dev/null || echo unknown)"
      echo "     Stop that process, or change the port in this script and in the service config."
      exit 1
    fi
    PORT="$port" nohup node "$script" > "$(log_file "$name")" 2>&1 &
    echo $! > "$(pid_file "$name")"
    printf "  ${GR}started${R} %-22s :%s  pid %s\n" "$name" "$port" "$!"
  done

  echo
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name port script <<< "$entry"
    if curl -s --retry 30 --retry-delay 1 --retry-connrefused --max-time 20 "http://localhost:$port/healthz" >/dev/null 2>&1; then
      printf "  ${GR}healthy${R} http://localhost:%s\n" "$port"
    else
      printf "  ${RE}unhealthy${R} http://localhost:%s  -- see %s\n" "$port" "$(log_file "$name")"
    fi
  done
  banner
}

stop() {
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name port script <<< "$entry"
    pf="$(pid_file "$name")"
    if is_running "$name"; then
      kill "$(cat "$pf")" 2>/dev/null || true
      printf "  ${D}stopped${R} %-22s pid %s\n" "$name" "$(cat "$pf")"
    else
      printf "  ${D}not running${R} %s\n" "$name"
    fi
    rm -f "$pf"
  done
}

status() {
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name port script <<< "$entry"
    if is_running "$name"; then
      health="$(curl -s --max-time 2 "http://localhost:$port/healthz" >/dev/null 2>&1 && echo healthy || echo "not responding")"
      printf "  %-22s :%s  ${GR}up${R}  %s  pid %s\n" "$name" "$port" "$health" "$(cat "$(pid_file "$name")")"
    else
      printf "  %-22s :%s  ${D}down${R}\n" "$name" "$port"
    fi
  done
}

logs() {
  local which="${1:-}"
  if [ -n "$which" ]; then tail -f "$RUN_DIR/$which.log"; else tail -f "$RUN_DIR"/*.log; fi
}

banner() {
  printf "\n${B}  OAuth 2.1 Simulator is up${R}\n\n"
  printf "    ${B}Start here${R}      http://localhost:9020        the client app and its labs\n"
  printf "    IdP + AS        http://localhost:9000        login, consent, tokens, policy switches\n"
  printf "    Resource API    http://localhost:9010        the protected data\n"
  printf "    Live trace      http://localhost:9000/trace  watch the protocol decide\n\n"
  printf "    Sign in as      ${B}alice / wonderland${R}   or   bob / builder\n"
  printf "    ${D}Logs: ./scripts/run-local.sh logs   Stop: ./scripts/run-local.sh stop${R}\n\n"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) shift; logs "${1:-}" ;;
  *) echo "usage: $0 {start|stop|restart|status|logs [service]}"; exit 2 ;;
esac
