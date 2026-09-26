#!/usr/bin/env python3
"""Build the persistent web .env without copying bot-local URLs.

The bot and API bridge listen on loopback on the bot box.  The web container
must reach both through the public tunnel, so both web URL variables are
derived from PUBLIC_GATEWAY_URL.  Existing web-only keys are preserved.
"""
from __future__ import annotations

import ipaddress
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


SHARED_KEYS = (
    "APP_BASE_URL",
    "BOT_SYNC_SECRET",
    "JWT_SECRET",
    "WEB_CREDS_KEY",
    "WEB_GATEWAY_SECRET",
)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "a").isalnum() or not key[0].isalpha():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def public_gateway(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ValueError("PUBLIC_GATEWAY_URL must be a public https URL")
    host = parsed.hostname.lower()
    if host in {"localhost", "::1"} or host.endswith((".local", ".internal")):
        raise ValueError("PUBLIC_GATEWAY_URL must not be loopback or internal")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None  # a DNS hostname, which is the normal case
    if address is not None:
        if address.is_private or address.is_loopback:
            raise ValueError("PUBLIC_GATEWAY_URL must not be a private address")
    return value


def build_web_env(bot: dict[str, str], existing: dict[str, str]) -> dict[str, str]:
    missing = [key for key in ("PUBLIC_GATEWAY_URL", *SHARED_KEYS) if not bot.get(key)]
    if missing:
        raise ValueError("bot environment is missing required keys: " + ", ".join(missing))
    if len(bot["BOT_SYNC_SECRET"]) < 32:
        raise ValueError("BOT_SYNC_SECRET is shorter than 32 characters")
    if len(bot["WEB_GATEWAY_SECRET"]) < 32:
        raise ValueError("WEB_GATEWAY_SECRET is shorter than 32 characters")

    endpoint = public_gateway(bot["PUBLIC_GATEWAY_URL"])
    out = dict(existing)
    for key in SHARED_KEYS:
        out[key] = bot[key]
    out.update({
        "NODE_ENV": "production",
        "PORT": "3000",
        "BOT_GATEWAY_URL": endpoint,
        "BOT_API_URL": endpoint,
    })
    return out


def write_atomic(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} contains a newline")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for key in sorted(values):
                handle.write(f"{key}={values[key]}\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else Path(".env")
    destination = (Path(argv[2]) if len(argv) > 2
                   else Path.home() / "runeclaw-persist" / "web.env")
    try:
        values = build_web_env(read_env(source), read_env(destination))
        write_atomic(destination, values)
    except (OSError, ValueError) as exc:
        print(f"prepare_web_env: {exc}", file=sys.stderr)
        return 1
    print(f"web environment ready: {destination} (BOT gateway + API bridge use PUBLIC_GATEWAY_URL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
