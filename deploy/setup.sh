#!/usr/bin/env bash
# Set up a ProofXiv instance on this machine: write deploy/.env with fresh secrets and, with
# --start, build and start the containers, create the federation key and check the site.
# Asks nothing, so an agent can run it. Safe to run again: an existing deploy/.env is kept.
#
#   deploy/setup.sh --domain proofxiv.example.org --files-domain files.proofxiv.example.org \
#       --owner-login alice --owner-key <owner public key> [--name "Example Lab"] [--start]
#
# The owner public key comes from `proofxiv owner new-key` on the owner's own machine.
set -euo pipefail
self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$self")"

die() { echo "error: $*" >&2; exit 1; }
warn() { echo "warning: $*" >&2; }
env_get() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | sed 's/^"//; s/"$//'; }

domain="" files="" login="" key="" name="ProofXiv" start=0
while [ $# -gt 0 ]; do
	case "$1" in
	--domain) domain=${2:?--domain needs a value}; shift 2 ;;
	--files-domain) files=${2:?--files-domain needs a value}; shift 2 ;;
	--owner-login) login=${2:?--owner-login needs a value}; shift 2 ;;
	--owner-key) key=${2:?--owner-key needs a value}; shift 2 ;;
	--name) name=${2:?--name needs a value}; shift 2 ;;
	--start) start=1; shift ;;
	-h | --help) sed -n '2,9p' "$self" | sed 's/^# \{0,1\}//'; exit 0 ;;
	*) die "unknown argument: $1 (see --help)" ;;
	esac
done

command -v docker >/dev/null || die "docker is not installed: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is not installed"

hostname_re='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
if [ -f .env ]; then
	echo "deploy/.env exists, keeping it"
	[ -z "$domain$files$login$key" ] || warn "the --domain, --files-domain and --owner-* values are ignored: edit deploy/.env instead"
else
	[ -n "$domain" ] && [ -n "$files" ] && [ -n "$login" ] && [ -n "$key" ] ||
		die "a new deploy/.env needs --domain, --files-domain, --owner-login and --owner-key"
	[[ $domain =~ $hostname_re ]] || die "--domain is not a lowercase host name: $domain"
	[[ $files =~ $hostname_re ]] || die "--files-domain is not a lowercase host name: $files"
	[ "$files" != "$domain" ] || die "the files domain must differ from the site domain"
	[[ $login =~ ^[a-z0-9][a-z0-9-]{0,38}$ ]] || die "--owner-login: lowercase letters, digits and hyphens"
	[[ $key =~ ^[A-Za-z0-9+/]{43}=$ ]] || die "--owner-key is not a base64 ed25519 public key (44 characters)"
	name_re='^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,79}$'
	[[ $name =~ $name_re ]] || die "--name: letters, digits, spaces and . _ ( ) - only"
	secret() { head -c 36 /dev/urandom | base64 | tr '+/' '-_'; }
	umask 077
	sed -e "s|^PROOFXIV_DOMAIN=.*|PROOFXIV_DOMAIN=$domain|" \
		-e "s|^PROOFXIV_FILES_DOMAIN=.*|PROOFXIV_FILES_DOMAIN=$files|" \
		-e "s|^PROOFXIV_PUBLIC_URL=.*|PROOFXIV_PUBLIC_URL=https://$domain|" \
		-e "s|^PROOFXIV_WEB_URL=.*|PROOFXIV_WEB_URL=https://$domain|" \
		-e "s|^PROOFXIV_ARTIFACTS_URL=.*|PROOFXIV_ARTIFACTS_URL=https://$files|" \
		-e "s|^PROOFXIV_INSTANCE_NAME=.*|PROOFXIV_INSTANCE_NAME=\"$name\"|" \
		-e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret)|" \
		-e "s|^PROOFXIV_SESSION_SECRET=.*|PROOFXIV_SESSION_SECRET=$(secret)$(secret)|" \
		-e "s|^PROOFXIV_OWNER_LOGINS=.*|PROOFXIV_OWNER_LOGINS=[\"$login\"]|" \
		-e "s|^PROOFXIV_OWNER_PUBLIC_KEYS=.*|PROOFXIV_OWNER_PUBLIC_KEYS=[\"$key\"]|" \
		.env.example >.env.new
	mv .env.new .env
	echo "wrote deploy/.env (mode 600); optional settings (GitHub sign-in, urgent notifications) are in it"
fi

for placeholder in change-me example.org your-login base64-ed25519-public-key; do
	! grep -qE "^[A-Z_]+=.*$placeholder" .env || die "deploy/.env still has the placeholder '$placeholder'"
done
docker compose config --quiet || die "docker compose does not accept deploy/.env"

domain=$(env_get PROOFXIV_DOMAIN)
files=$(env_get PROOFXIV_FILES_DOMAIN)
for host in "$domain" "$files"; do
	addresses=$(getent ahosts "$host" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)
	if [ -z "$addresses" ]; then
		warn "$host does not resolve yet: Caddy gets its TLS certificate once it points to this machine"
	else
		echo "$host -> $addresses"
	fi
done

if [ "$start" = 0 ]; then
	echo "next: deploy/setup.sh --start (or: cd deploy && docker compose up -d --build)"
	exit 0
fi

docker compose up -d --build
echo "waiting for the API (database migrations run first)..."
up=0
for _ in $(seq 60); do
	if docker compose exec -T api python -c \
		"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" >/dev/null 2>&1; then
		up=1
		break
	fi
	sleep 5
done
[ "$up" = 1 ] || die "the API did not start: docker compose logs api"

docker compose exec -T api proofxiv-server init-instance
./check.sh || warn "some checks failed; a new TLS certificate can take a minute or two, then run deploy/check.sh again"

login=$(env_get PROOFXIV_OWNER_LOGINS | sed 's/^\["\([^"]*\)".*/\1/')
echo
echo "The instance runs. Next, on the owner's own machine (where the owner key is), docs/DEPLOY.md step 3:"
echo "  proofxiv config set server https://$domain"
echo "  proofxiv owner sign-in-link --login $login   # a one-time website link for the owner"
echo "  proofxiv owner approve-prompts"
echo "  proofxiv owner attest-key --role federation --public-key <the public key printed above>"
