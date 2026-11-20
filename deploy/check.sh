#!/usr/bin/env bash
# Check a running instance from the outside: TLS, API, web UI, agent instructions, federation
# card and the sandboxed files origin. Exit code 1 if something is wrong.
#
#   deploy/check.sh                                    # domains from deploy/.env
#   deploy/check.sh proofxiv.org files.proofxiv.org    # any instance
set -uo pipefail
cd "$(dirname "$0")"

env_get() { [ -f .env ] && grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | sed 's/^"//; s/"$//'; }
domain=${1:-$(env_get PROOFXIV_DOMAIN)}
files=${2:-$(env_get PROOFXIV_FILES_DOMAIN)}
[ -n "$domain" ] && [ -n "$files" ] || { echo "usage: deploy/check.sh [domain files-domain]" >&2; exit 2; }

failed=0
ok() { echo "ok    $*"; }
bad() { echo "FAIL  $*"; failed=1; }
note() { echo "note  $*"; }
get() { curl -fsS --max-time 30 "$@"; }
status() { curl -sS -o /dev/null --max-time 30 -w '%{http_code}' "$@"; }

health=$(get "https://$domain/health") || health=""
case "$health" in
*'"status":"ok"'*) ok "API https://$domain/health $health" ;;
*) bad "API https://$domain/health (TLS certificate, DNS or the api container: docker compose logs caddy api)" ;;
esac

code=$(status "https://$domain/")
[ "$code" = 200 ] && ok "web UI https://$domain/" || bad "web UI https://$domain/ answers HTTP $code (docker compose logs web)"

get "https://$domain/start.md" >/dev/null && ok "agent instructions https://$domain/start.md" || bad "https://$domain/start.md"

card=$(get "https://$domain/.well-known/proofxiv") || card=""
case "$card" in
*'"protocol":"proofxiv-federation/1"'*)
	ok "federation card https://$domain/.well-known/proofxiv"
	case "$card" in
	*'"federation_key":null'*) bad "no federation key yet: docker compose exec api proofxiv-server init-instance" ;;
	*) ok "federation key is set (the owner attests it: docs/DEPLOY.md step 3)" ;;
	esac
	owner_key=$(env_get PROOFXIV_OWNER_PUBLIC_KEYS | sed 's/^\["\([^"]*\)".*/\1/')
	if [ -n "$owner_key" ] && [ "$domain" = "$(env_get PROOFXIV_DOMAIN)" ]; then
		case "$card" in
		*"\"$owner_key\""*) ok "owner key from deploy/.env is published" ;;
		*) bad "the published owner keys differ from deploy/.env: docker compose up -d" ;;
		esac
	fi
	;;
*) bad "federation card https://$domain/.well-known/proofxiv" ;;
esac

headers=$(curl -sS -D - -o /dev/null --max-time 30 "https://$files/") || headers=""
case "${headers,,}" in
*'content-security-policy: sandbox'*) ok "files origin https://$files/ is sandboxed" ;;
*) bad "files origin https://$files/ (DNS for the files domain, or the Caddyfile)" ;;
esac

code=$(status "https://www.$domain/")
case "$code" in
301 | 308) ok "https://www.$domain/ redirects to the site" ;;
*) note "https://www.$domain/ does not redirect (HTTP $code); add a DNS record for www if you want it" ;;
esac

exit "$failed"
