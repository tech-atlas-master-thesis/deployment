#!/usr/bin/env bash
# One-time setup of the production stack on a fresh server.
#
#   bash init.sh
#
# - creates .env from .env.template (an existing .env is kept as it is)
# - replaces every "__GENERATE__" placeholder in .env with a random secret
# - checks that the private key for traefik/certs/origin.crt is in place; if there is no
#   certificate at all yet, creates a self-signed stand-in
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
crt="$CERT_DIR/origin.crt"
key="$CERT_DIR/origin.key"
# Public key of a certificate / of a private key, to check that the two belong together.
pub_of_crt() { openssl x509 -in "$crt" -noout -pubkey 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | openssl sha256; }
pub_of_key() { openssl pkey -in "$key" -pubout -outform DER 2>/dev/null | openssl sha256; }

if [[ -e "$crt" && ! -e "$key" ]]; then
    # The certificate is in the repository, its private key never is.
    echo "error: $crt is present but $key is missing." >&2
    echo "       Copy the private key that belongs to the certificate to $key and re-run." >&2
    exit 1
elif [[ -e "$crt" ]]; then
    if [[ "$(pub_of_crt)" != "$(pub_of_key)" ]]; then
        echo "error: $key does not belong to $crt." >&2
        exit 1
    fi
    chmod 600 "$key"
    echo "Certificate in $CERT_DIR: key matches -- keeping it."
else
    # No certificate yet: self-signed stand-in so the stack can start.
    hosts=()
    for name in DASHBOARD_HOST ADMIN_HOST AUTH_HOST; do
        host="$(env_get "$name")"
        [[ -n "$host" ]] && hosts+=("DNS:$host")
    done
    san="$(IFS=,; echo "${hosts[*]}")"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -keyout "$key" -out "$crt" \
        -subj "/CN=$(env_get DASHBOARD_HOST)" -addext "subjectAltName=$san" 2>/dev/null
    chmod 600 "$key"
    echo "Created a self-signed stand-in certificate for: ${san//DNS:/}"
    echo "  -> only accepted in Cloudflare SSL/TLS mode \"Full\". Replace it with a Cloudflare"
    echo "     Origin CA certificate and use \"Full (strict)\" -- see README.md, \"Certificate\"."
fi

cat <<EOF

Next steps:
  1. Review $ENV_FILE (hosts, HTTPS_PORT, VERSION).
  2. docker compose pull && docker compose up -d
  3. Sign in to https://$(env_get AUTH_HOST)/ as "akadmin" with AUTHENTIK_BOOTSTRAP_PASSWORD
     from $ENV_FILE and change that password.
  4. Create an invitation for your own account in authentik (see README.md, "Invitations").
EOF
