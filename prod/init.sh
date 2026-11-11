#!/usr/bin/env bash
# One-time setup of the production stack on a fresh server.
#
#   bash init.sh
#
# - creates .env from .env.template (an existing .env is kept as it is)
# - replaces every "__GENERATE__" placeholder in .env with a random secret
# - creates a self-signed certificate for Traefik if traefik/certs/ has none
#
# Safe to re-run: values that are already set are never changed. To start over with new
# secrets, delete .env *and* the Docker volumes -- the databases keep the passwords they
# were first started with.
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env"
TEMPLATE=".env.template"
CERT_DIR="traefik/certs"
PLACEHOLDER='"__GENERATE__"'

command -v openssl >/dev/null || { echo "error: openssl is required" >&2; exit 1; }

# Hex only: safe in .env, in Compose interpolation and in connection strings alike.
secret() { openssl rand -hex 32; }

# Value of KEY in .env, without surrounding quotes.
env_get() {
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

if [[ -e "$ENV_FILE" ]]; then
    echo "$ENV_FILE already exists -- keeping it."
else
    cp "$TEMPLATE" "$ENV_FILE"
    echo "Created $ENV_FILE from $TEMPLATE."
fi
chmod 600 "$ENV_FILE"

generated=0
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" != \#* && "$line" == *=$PLACEHOLDER ]]; then
        line="${line%%=*}=\"$(secret)\""
        generated=$((generated + 1))
    fi
    printf '%s\n' "$line"
done <"$ENV_FILE" >"$tmp"
cat "$tmp" >"$ENV_FILE"
echo "Generated $generated secret(s)."

mkdir -p "$CERT_DIR"
if [[ -e "$CERT_DIR/origin.crt" && -e "$CERT_DIR/origin.key" ]]; then
    echo "Certificate in $CERT_DIR already exists -- keeping it."
else
    hosts=()
    for key in DASHBOARD_HOST ADMIN_HOST AUTH_HOST; do
        host="$(env_get "$key")"
        [[ -n "$host" ]] && hosts+=("DNS:$host")
    done
    san="$(IFS=,; echo "${hosts[*]}")"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -keyout "$CERT_DIR/origin.key" -out "$CERT_DIR/origin.crt" \
        -subj "/CN=$(env_get DASHBOARD_HOST)" -addext "subjectAltName=$san" 2>/dev/null
    chmod 600 "$CERT_DIR/origin.key"
    echo "Created a self-signed certificate for: ${san//DNS:/}"
    echo "  -> works with Cloudflare SSL/TLS mode \"Full\". For \"Full (strict)\" replace"
    echo "     $CERT_DIR/origin.crt and origin.key with a Cloudflare Origin CA certificate."
fi

cat <<EOF

Next steps:
  1. Review $ENV_FILE (hosts, HTTPS_PORT, VERSION).
  2. docker compose pull && docker compose up -d
  3. Sign in to https://$(env_get AUTH_HOST)/ as "akadmin" with AUTHENTIK_BOOTSTRAP_PASSWORD
     from $ENV_FILE and change that password.
  4. Create an invitation for your own account in authentik (see README.md, "Invitations").
EOF
