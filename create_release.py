#!/usr/bin/env python3
"""
Create a versioned release of tech-atlas.

Usage:
    ./create_release.py [version] [--force] [--skip-tag]

If no version is given, defaults to "<year>.<month>.<minor>" (e.g. 2026.08.0),
where <minor> is the next free number for the current year/month under release/.

What it does, in order (each step is safe to re-run):
    1. Tag every app image ":latest" -> ":<version>".
    2. Create release/<version>/ and copy in docker-compose.yml + init-mongo.js
       from latest/, and .env.template from the repo root.
    3. Upsert VERSION / VERSION_SLUG / BASE_DOMAIN / COMPOSE_PROJECT_NAME into
       the copied .env.template.
    4. Rename the Traefik router labels in the *copied* docker-compose.yml to
       include the version (e.g. tech-atlas-dashboard-ui -> ...-2026-08-0).
       This one piece can't be done via .env: per the Compose spec,
       interpolation only ever applies to YAML *values*, never to mapping
       keys (https://docs.docker.com/reference/compose-file/interpolation/),
       and a Traefik router name is necessarily a label *key*
       (traefik.http.routers.<name>.rule). So this is a small, whitelisted,
       literal string replacement against the known list of router names
       below -- not a general regex sweep of the file.
    5. Validate the result with `docker compose config` (if the docker CLI is
       available) or a structural YAML/interpolation check otherwise.

The .env file itself is intentionally NOT copied -- .env.template is the
starting point for a release; secrets get filled in separately.

Everything else in docker-compose.yml (image tags, container/network/volume
names, the Traefik Host() rule) is driven purely by ${VERSION} / ${VERSION_SLUG}
/ ${BASE_DOMAIN} values in .env -- the file itself is identical across every
release, copied byte-for-byte from latest/.
"""

import argparse
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LATEST_DIR = ROOT / "latest"
RELEASE_ROOT = ROOT / "release"
ENV_TEMPLATE_SRC = ROOT / ".env.template"

# The app images we build & tag ourselves. Third-party images (e.g. mongo)
# are intentionally excluded -- they stay on whatever tag they already use.
APP_IMAGES = [
    "moosi312/tech-atlas-landing-page",
    "moosi312/tech-atlas-dashboard-ui",
    "moosi312/tech-atlas-dashboard-be",
    "moosi312/tech-atlas-admin-ui",
    "moosi312/tech-atlas-scraper-be",
    "moosi312/tech-atlas-transformer-be",
]

# Traefik router base names used as label keys in docker-compose.yml
# (traefik.http.routers.<name>.rule / .entrypoints). Kept as its own list
# since it's a label *key*, not a value -- see the module docstring.
ROUTER_NAMES = [
    "tech-atlas-landing-page",
    "tech-atlas-dashboard-ui",
    "tech-atlas-dashboard-be",
    "tech-atlas-admin-ui",
    "tech-atlas-scraper-be",
    "tech-atlas-transformer-be",
]

BASE_DOMAIN_ROOT = "tech-atlas.mooslechner.dev"

VERSION_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


class ReleaseError(RuntimeError):
    pass


def default_version() -> str:
    """<year>.<month>.<minor>, minor = next free number this year/month."""
    now = datetime.datetime.now()
    year, month = now.year, now.month
    prefix = f"{year}.{month:02d}."

    existing = []
    if RELEASE_ROOT.is_dir():
        for entry in RELEASE_ROOT.iterdir():
            if entry.is_dir() and entry.name.startswith(prefix):
                suffix = entry.name[len(prefix):]
                if suffix.isdigit():
                    existing.append(int(suffix))

    minor = max(existing) + 1 if existing else 0
    return f"{prefix}{minor}"


def validate_version(version: str) -> None:
    if not VERSION_RE.match(version):
        raise ReleaseError(
            f"Invalid version '{version}': only letters, digits, '.', '_' "
            "and '-' are allowed (no leading/trailing separator)."
        )


def version_slug(version: str) -> str:
    """Dot-free identifier safe for Compose project names, container/network/
    volume names, and Traefik router names (Traefik's label provider treats
    '.' in a router name as a path separator, so dots can't be used there)."""
    return version.replace(".", "-")


def env_values(version: str) -> dict:
    slug = version_slug(version)
    return {
        "VERSION": version,
        "VERSION_SLUG": slug,
        "BASE_DOMAIN": f"{slug}.{BASE_DOMAIN_ROOT}" if slug != "latest" else BASE_DOMAIN_ROOT,
        "COMPOSE_PROJECT_NAME": f"tech-atlas-{slug}",
    }


def tag_images(version: str, dry_run: bool) -> None:
    for image in APP_IMAGES:
        src, dst = f"{image}:latest", f"{image}:{version}"
        print(f"docker tag {src} {dst}")
        if dry_run:
            continue
        try:
            subprocess.run(["docker", "tag", src, dst], check=True)
            subprocess.run(["docker", "push", dst], check=True)
        except FileNotFoundError:
            raise ReleaseError("docker CLI not found on PATH.")
        except subprocess.CalledProcessError as exc:
            raise ReleaseError(
                f"Failed to tag {src} -> {dst} (is the image pulled locally?)"
            ) from exc


def rename_traefik_routers(compose_path: Path, version: str, slug: str) -> None:
    """Suffix every known Traefik router name with the version slug, so a
    release's routers don't collide with latest's (or another release's) in
    Traefik's routing table. No-op for "latest" -- it keeps today's plain
    names. Idempotent: matches on the *unsuffixed* name, so re-running this
    against an already-suffixed file does nothing further."""
    if version == "latest":
        return

    text = compose_path.read_text()
    for name in ROUTER_NAMES:
        text = text.replace(f"routers.{name}.", f"routers.{name}-{slug}.")
    compose_path.write_text(text)


def upsert_env_file(path: Path, values: dict) -> None:
    """Update KEY="..." lines in place if present, append if not. Preserves
    every other line (including comments and ordering) untouched, and is
    idempotent: running this twice with the same values yields the same
    file."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)

    for i, line in enumerate(lines):
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines[i] = f'{key}="{remaining.pop(key)}"'

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Release/versioning -- managed by create_release.py")
        for key, value in remaining.items():
            lines.append(f'{key}="{value}"')

    path.write_text("\n".join(lines) + "\n")


def prepare_release_dir(version: str, force: bool) -> Path:
    version_dir = RELEASE_ROOT / version
    if version_dir.exists() and not force:
        raise ReleaseError(
            f"{version_dir} already exists. Re-run with --force to overwrite."
        )
    version_dir.mkdir(parents=True, exist_ok=True)
    return version_dir


def copy_release_files(version_dir: Path) -> Path:
    shutil.copy2(LATEST_DIR / "docker-compose.yml", version_dir / "docker-compose.yml")
    shutil.copy2(LATEST_DIR / "init-mongo.js", version_dir / "init-mongo.js")
    env_template_dst = version_dir / ".env.template"
    shutil.copy2(ENV_TEMPLATE_SRC, env_template_dst)
    return env_template_dst


def _find_keys_with_variables(node, path="") -> list:
    """Recursively find any mapping KEY that still contains a literal
    '${...}' -- this is exactly the class of bug Compose can't interpolate
    (interpolation only ever applies to values, never to keys). Catching
    this here means we find it before `docker compose` -- or a real
    deployment -- does."""
    problems = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and "${" in key:
                problems.append(f"{path}.{key}" if path else key)
            problems.extend(_find_keys_with_variables(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            problems.extend(_find_keys_with_variables(item, f"{path}[{i}]"))
    return problems


def _interpolate_values(node, env: dict):
    """Mimic real Compose interpolation: substitute ${VAR} only inside
    string VALUES, recursively -- never inside mapping keys. Returns a new
    structure; does not mutate `node`."""
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    if isinstance(node, dict):
        return {k: _interpolate_values(v, env) for k, v in node.items()}
    if isinstance(node, list):
        return [_interpolate_values(v, env) for v in node]
    if isinstance(node, str):
        return pattern.sub(lambda m: env.get(m.group(1), m.group(0)), node)
    return node


def validate_compose(version_dir: Path, values: dict) -> None:
    compose_file = version_dir / "docker-compose.yml"

    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "config"],
            check=True,
            capture_output=True,
            text=True,
            env={**_current_env(), **values},
        )
        print("docker compose config: OK")
        return
    except FileNotFoundError:
        pass  # fall back below
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(f"docker compose config failed:\n{exc.stderr}") from exc

    # Fallback when the docker CLI isn't available: parse the *unmodified*
    # YAML first (so keys and values both still contain literal ${...}),
    # then check no key needed interpolation, then interpolate values only --
    # this mirrors what `docker compose` actually does, unlike a blind
    # text-substitute-then-parse pass.
    import yaml

    doc = yaml.safe_load(compose_file.read_text())

    bad_keys = _find_keys_with_variables(doc)
    if bad_keys:
        raise ReleaseError(
            "docker CLI not found, and these keys still contain an "
            "unresolved '${...}' -- Compose never interpolates mapping "
            f"keys, only values: {bad_keys}"
        )

    _interpolate_values(doc, values)
    print("docker CLI not found -- validated YAML structure + key/value interpolation rules only.")


def _current_env() -> dict:
    import os
    return dict(os.environ)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="Version name (default: <year>.<month>.<minor>)")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing release/<version>/")
    parser.add_argument("--skip-tag", action="store_true", help="Skip `docker tag` (for testing without Docker)")
    args = parser.parse_args()

    version = args.version or default_version()
    try:
        validate_version(version)
        slug = version_slug(version)
        values = env_values(version)

        tag_images(version, dry_run=args.skip_tag)

        version_dir = prepare_release_dir(version, force=args.force)
        env_template_dst = copy_release_files(version_dir)
        rename_traefik_routers(version_dir / "docker-compose.yml", version, slug)
        upsert_env_file(env_template_dst, values)
        validate_compose(version_dir, values)
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"\nRelease '{version}' created at {version_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
