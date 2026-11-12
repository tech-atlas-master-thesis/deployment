# Tech Atlas – production stack

Self-contained deployment for a single server behind Cloudflare: Traefik (HTTPS), the Tech
Atlas services, MongoDB, Photon and an authentik identity provider for the admin UI.

| Host (default)        | Serves                                   |
| --------------------- | ---------------------------------------- |
| `tech-atlas.at`       | dashboard-ui, `/api` → dashboard-be      |
| `admin.tech-atlas.at` | admin-ui, `/api/admin` → admin-be        |
| `auth.tech-atlas.at`  | authentik (login, invitations, admin UI) |

Hosts, port and image version are set in `.env`.

## Files

| File                                   | Purpose                                                         |
| -------------------------------------- | --------------------------------------------------------------- |
| `docker-compose.yml`                   | Application services, MongoDB, Photon                           |
| `compose.traefik.yml`                  | Traefik, HTTPS entrypoint                                       |
| `compose.auth.yml`                     | authentik server, worker and its PostgreSQL                     |
| `authentik/blueprints/tech-atlas.yaml` | authentik configuration as code (applied automatically)         |
| `traefik/dynamic/tls.yml`              | Which certificate Traefik serves                                |
| `traefik/certs/origin.crt`             | Cloudflare Origin CA certificate (its key is not in the repo)   |
| `.env.template` → `.env`               | Configuration and secrets (`.env` is git-ignored)               |
| `init.sh`                              | Creates `.env`, generates secrets, checks the certificate       |

The three compose files form one project; `COMPOSE_FILE` in `.env` joins them, so plain
`docker compose ...` in this directory always addresses the whole stack.

## First start

Requirements: Docker Engine with the Compose plugin and `openssl`.

Before starting, check the [Certificate section](#certificate) and add the required certificate and key.

```bash
bash init.sh                               # .env, secrets, certificate
nano .env                                  # check hosts, HTTPS_PORT, VERSION
docker compose pull && docker compose up -d
```

Photon downloads the Austria index on its first start and authentik runs its migrations;
give both a few minutes. Then:

1. Open `https://auth.tech-atlas.at/`, sign in as `akadmin` with
   `AUTHENTIK_BOOTSTRAP_PASSWORD` from `.env` and change the password.
2. Check that the blueprint was applied: *Customization → Blueprints* shows **Tech Atlas**
   as successful, and *Applications* lists **Tech Atlas Admin**. If it is missing or failed:
   `docker compose exec authentik-worker ak apply_blueprint /blueprints/custom/tech-atlas.yaml`
3. Create an editor invitation (see [Invitations](#invitations)) and open the link to
   create your own account. `akadmin` is only for administering authentik; it is in
   neither Tech Atlas group and cannot use the admin UI.

### Cloudflare

- DNS: proxied (orange cloud) `A` records for the three hosts pointing at the server's IP.
- SSL/TLS mode **Full (strict)**, with the origin certificate below.
- `HTTPS_PORT` must be a port Cloudflare proxies HTTPS to (443, 2053, 2083, 2087, 2096,
  8443), or be mapped with an Origin Rule.
- Recommended: firewall the port so that only Cloudflare's IP ranges can reach it.

### Certificate

Browsers get Cloudflare's own certificate. Between Cloudflare and this server, Traefik
presents a **Cloudflare Origin CA certificate**: free, valid for up to 15 years, trusted by
Cloudflare only. It is why the proxy has to stay on.

Once, and again when it expires:

1. Cloudflare dashboard → *SSL/TLS → Origin Server → Create Certificate*, hostnames
   `tech-atlas.at` and `*.tech-atlas.at`, format PEM.
2. Save the certificate as `traefik/certs/origin.crt` and commit it. It is public.
3. Save the private key as `traefik/certs/origin.key` **on the server only**, and keep a
   copy in a password manager; Cloudflare shows it once. It is git-ignored.
4. `bash init.sh` checks that key and certificate belong together, then
   `docker compose restart traefik`.
5. Set the SSL/TLS mode to **Full (strict)**.

Without an `origin.crt`, `init.sh` creates a self-signed stand-in that only works in mode
**Full**; delete both files before installing the real pair.

## Invitations

Sign-up is only possible through an invitation link. In the authentik admin interface:

1. *Directory → Invitations → New Invitation*.
2. Flow: **Tech Atlas enrollment** (`tech-atlas-enrollment`).
3. Set a name, an expiry and whether the link is single use.
4. For someone who may edit, put this into *Custom attributes*; leave it empty for a viewer:

   ```yaml
   attributes:
     tech_atlas_role: editor
   ```

5. Save, expand the invitation in the list and copy its link
   (`https://auth.tech-atlas.at/if/flow/tech-atlas-enrollment/?itoken=...`).

authentik consumes a single-use invitation as soon as the link is opened, not when the form
is submitted.

Viewers get the group `tech-atlas-viewer` (entitlement `tech-atlas:read`), editors
additionally `tech-atlas-editor` (`tech-atlas:write`). Roles can be changed later under
*Directory → Groups*.

## Updating

```bash
docker compose pull && docker compose up -d
```

App images follow `VERSION` (default `stable`, moved by `create_release.py` on every
release). Third-party images are pinned in the compose files; to upgrade one, change its
tag there. authentik has to be upgraded one release line at a
time, without skipping any.

## Backup and restore

What holds state:

| Volume                      | Content                         | Back up with                                                                  |
| --------------------------- | ------------------------------- | ----------------------------------------------------------------------------- |
| `tech-atlas-db-data`        | MongoDB                         | `docker compose exec mongodb mongodump ...`                                   |
| `tech-atlas-authentik-db`   | authentik users, invitations    | `docker compose exec -T authentik-db pg_dump -U authentik authentik > ak.sql` |
| `tech-atlas-photon-index`   | Geocoder index (re-downloaded)  | not needed                                                                    |
| `tech-atlas-authentik-data` | authentik media (unused so far) | not needed                                                                    |

Plus `.env` (keep it somewhere safe: without `AUTHENTIK_SECRET_KEY` a database dump of
authentik is of little use) and `traefik/certs/`.

Restoring authentik without a database backup: start with an empty `tech-atlas-authentik-db`
volume. The blueprint recreates provider, application, groups, entitlements and the
enrollment flow; `akadmin` is recreated from `.env`. Only the user accounts are gone, so
send new invitations. Restart `admin-be` afterwards, it caches authentik's signing keys.

## Notes

- admin-ui gets its OIDC issuer and client id at container start (`OAUTH_ISSUER`,
  `OAUTH_CLIENT_ID` in `docker-compose.yml`). This needs an admin-ui image that supports
  runtime configuration; older images ignore the variables and use their compiled-in issuer.
- `admin-be` fetches authentik's signing keys from `https://<AUTH_HOST>/...`, i.e. through
  Cloudflare, so DNS has to be live before the admin API accepts logins.
- MongoDB 8.0 refuses to start on Linux kernels 6.19 – 7.0.13 (upstream incompatibility,
  fixed in 7.0.14). Check `uname -r` on the server.
- Memory limits add up to about 14 GB if every container hits its ceiling; typical use is
  far below that.
