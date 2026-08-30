#!/usr/bin/env python3
"""Narrow Calendar-events OAuth setup for API-created Google Meet rooms."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from urllib import error, parse, request

SCOPE = "https://www.googleapis.com/auth/calendar.events"
REDIRECT_URI = "http://localhost:1"


def paths() -> tuple[Path, Path, Path]:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return (
        Path(os.environ.get("GOOGLE_CLIENT_SECRET_FILE", str(home / "google_client_secret.json"))),
        Path(os.environ.get("GOOGLE_MEETING_TOKEN_FILE", str(home / "google_meeting_token.json"))),
        home / "google_meeting_oauth_pending.json",
    )


def load_client(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = data.get("installed") or data.get("web")
    if not isinstance(cfg, dict) or not cfg.get("client_id") or not cfg.get("client_secret"):
        raise RuntimeError("invalid Google OAuth client file")
    return cfg


def auth_url(account: str = "") -> str:
    client_path, _, pending_path = paths()
    cfg = load_client(client_path)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(json.dumps({"state": state, "verifier": verifier, "client_path": str(client_path), "account": account.strip().lower()}), encoding="utf-8")
    pending_path.chmod(0o600)
    params = {
        "client_id": cfg["client_id"], "redirect_uri": REDIRECT_URI,
        "response_type": "code", "scope": SCOPE, "access_type": "offline",
        "prompt": "consent", "include_granted_scopes": "false", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if account.strip():
        params["login_hint"] = account.strip().lower()
    return str(cfg.get("auth_uri") or "https://accounts.google.com/o/oauth2/auth") + "?" + parse.urlencode(params)


def parse_callback(value: str) -> tuple[str, str]:
    if "://" in value:
        query = parse.parse_qs(parse.urlparse(value).query)
        return query.get("code", [""])[0], query.get("state", [""])[0]
    return value, ""


def exchange_callback(value: str) -> Path:
    _, token_path, pending_path = paths()
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    cfg = load_client(Path(pending["client_path"]))
    code, state = parse_callback(value)
    if not code:
        raise RuntimeError("callback has no authorization code")
    if state and state != pending.get("state"):
        raise RuntimeError("OAuth state mismatch")
    form = parse.urlencode({
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "code": code, "code_verifier": pending["verifier"],
        "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI,
    }).encode()
    req = request.Request(str(cfg.get("token_uri") or "https://oauth2.googleapis.com/token"), data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with request.urlopen(req, timeout=30) as response:
            response_data = json.loads(response.read())
    except error.HTTPError as exc:
        raise RuntimeError(f"Google OAuth exchange failed HTTP {exc.code}") from exc
    refresh_token = str(response_data.get("refresh_token") or "")
    if not refresh_token:
        raise RuntimeError("Google did not return a refresh token")
    token = {
        "type": "authorized_user", "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"], "refresh_token": refresh_token,
        "token_uri": str(cfg.get("token_uri") or "https://oauth2.googleapis.com/token"),
        "scopes": [SCOPE], "account": str(pending.get("account") or ""),
    }
    token_path.write_text(json.dumps(token), encoding="utf-8")
    token_path.chmod(0o600)
    pending_path.unlink(missing_ok=True)
    return token_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--auth-url", action="store_true")
    group.add_argument("--auth-code")
    parser.add_argument("--account", default="", help="Expected Google account; also used as login_hint")
    args = parser.parse_args(argv)
    print(auth_url(args.account) if args.auth_url else exchange_callback(args.auth_code))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
