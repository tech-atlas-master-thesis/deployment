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
    3. Upsert VERSION / VERSION_SLUG / BASE_DOMAIN / ROUTER_SUFFIX /
       COMPOSE_PROJECT_NAME into the copied .env.template. docker-compose.yml
       itself is never edited -- it already reads these values via ${...}.
    4. Validate the result with `docker compose config` (if the docker CLI is
       available) or a YAML/interpolation sanity check otherwise.

The .env file itself is intentionally NOT copied -- .env.template is the
starting point for a release; secrets get filled in separately.
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
        "BASE_DOMAIN": f"{version}.{BASE_DOMAIN_ROOT}" if version != "latest" else BASE_DOMAIN_ROOT,
        "ROUTER_SUFFIX": f"-{slug}" if version != "latest" else "",
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
        except FileNotFoundError:
            raise ReleaseError("docker CLI not found on PATH.")
        except subprocess.CalledProcessError as exc:
            raise ReleaseError(
                f"Failed to tag {src} -> {dst} (is the image pulled locally?)"
            ) from exc


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


def validate_compose(version_dir: Path, values: dict) -> None:
    compose_file = version_dir / "docker-compose.yml"
    env = {**values}
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "config"],
            check=True,
            capture_output=True,
            text=True,
            env={**_current_env(), **env},
        )
        print("docker compose config: OK")
        return
    except FileNotFoundError:
        pass  # fall back below
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(f"docker compose config failed:\n{exc.stderr}") from exc

    # Fallback when the docker CLI isn't available: parse the YAML after
    # doing Compose-style ${VAR} substitution ourselves, so we still catch
    # YAML/anchor mistakes before calling the release done.
    import yaml

    raw = compose_file.read_text()
    substituted = re.sub(
        r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
        lambda m: env.get(m.group(1), m.group(0)),
        raw,
    )
    yaml.safe_load(substituted)
    print("docker CLI not found -- validated YAML + variable substitution only.")


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
        values = env_values(version)

        tag_images(version, dry_run=args.skip_tag)

        version_dir = prepare_release_dir(version, force=args.force)
        env_template_dst = copy_release_files(version_dir)
        upsert_env_file(env_template_dst, values)
        validate_compose(version_dir, values)
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"\nRelease '{version}' created at {version_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
