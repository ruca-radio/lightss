#!/usr/bin/env python3
"""Generate an Apple Music (MusicKit) developer token.

Per https://developer.apple.com/documentation/applemusicapi/generating-developer-tokens
the token is an ES256-signed JWT: header {"alg": "ES256", "kid": <Key ID>},
claims {"iss": <Team ID>, "iat": now, "exp": now + ttl} signed with the
MusicKit private key (.p8) from the Apple Developer account.

Usage:
    python scripts/apple_dev_token.py --team-id ABC123DEFG --key-id XYZ987WVU \
        --key-file ~/Downloads/AuthKey_XYZ987WVU.p8

    # Generate and push straight into the running controller's settings:
    python scripts/apple_dev_token.py --team-id ... --key-id ... --key-file ... \
        --save http://127.0.0.1:8123
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

MAX_TTL_S = 15777000  # 6 months, per Apple docs
DEFAULT_TTL_S = MAX_TTL_S


def build_developer_token(
    team_id: str,
    key_id: str,
    key_pem: str,
    ttl_s: int = DEFAULT_TTL_S,
    origins: list[str] | None = None,
    now: int | None = None,
) -> str:
    import jwt

    team_id = team_id.strip()
    key_id = key_id.strip()
    if len(team_id) != 10:
        raise ValueError(f"Team ID must be 10 characters, got {len(team_id)}.")
    if len(key_id) != 10:
        raise ValueError(f"Key ID must be 10 characters, got {len(key_id)}.")
    if ttl_s <= 0 or ttl_s > MAX_TTL_S:
        raise ValueError(f"ttl must be 1..{MAX_TTL_S} seconds (Apple caps exp at 6 months out).")

    now = int(time.time()) if now is None else int(now)
    claims: dict = {"iss": team_id, "iat": now, "exp": now + int(ttl_s)}
    if origins:
        claims["origin"] = [str(o).strip() for o in origins if str(o).strip()]
    return jwt.encode(claims, key_pem, algorithm="ES256", headers={"kid": key_id})


def save_token_to_controller(base_url: str, token: str, timeout: float = 5.0) -> dict:
    url = f"{base_url.rstrip('/')}/api/settings"
    body = json.dumps({"audio_player": {"apple_developer_token": token}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an Apple Music developer token (ES256 JWT).")
    parser.add_argument("--team-id", required=True, help="10-character Apple Developer Team ID (JWT iss).")
    parser.add_argument("--key-id", required=True, help="10-character MusicKit key identifier (JWT kid).")
    parser.add_argument("--key-file", required=True, help="Path to the MusicKit private key (.p8, PEM).")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL_S, help=f"Lifetime in seconds (max {MAX_TTL_S}).")
    parser.add_argument("--origin", action="append", default=[], help="Allowed web origin (repeatable, optional).")
    parser.add_argument("--save", metavar="BASE_URL", help="POST the token to a running controller's /api/settings.")
    args = parser.parse_args(argv)

    try:
        with open(args.key_file, "r", encoding="utf-8") as f:
            key_pem = f.read()
        token = build_developer_token(args.team_id, args.key_id, key_pem, ttl_s=args.ttl, origins=args.origin)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.save:
        try:
            result = save_token_to_controller(args.save, token)
        except Exception as exc:
            print(f"error: could not save token to {args.save}: {exc}", file=sys.stderr)
            return 3
        if not result.get("ok"):
            print(f"error: controller rejected settings save: {result}", file=sys.stderr)
            return 3
        print(f"Token saved to controller at {args.save} (expires in {args.ttl // 86400} days).")
    else:
        print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
