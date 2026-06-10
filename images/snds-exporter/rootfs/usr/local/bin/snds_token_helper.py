#!/usr/bin/env python3
"""Keep a Microsoft SNDS REST API access token fresh for snds-exporter."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import secrets
import stat
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
AUTHORIZE_ENDPOINT = f"{AUTHORITY}/authorize"
TOKEN_ENDPOINT = f"{AUTHORITY}/token"
CLIENT_ID = "a53a6cc1-a1cd-46f7-a4aa-281cdabec33c"
SNDS_SCOPE = "a53a6cc1-a1cd-46f7-a4aa-281cdabec33c/.default"
AUTH_SCOPES = [SNDS_SCOPE, "offline_access", "openid", "profile"]
TOKEN_SCOPES = [SNDS_SCOPE]
MANUAL_REDIRECT_URI = "http://localhost"
DEFAULT_REFRESH_BEFORE_SECONDS = 600
DEFAULT_RETRY_SECONDS = 30

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
logger = logging.getLogger("snds-token-helper")


def _xdg_path(env_name: str, default_suffix: str) -> Path:
    base = os.getenv(env_name)
    if base:
        return Path(base) / "snds-exporter" / default_suffix
    if env_name == "XDG_CACHE_HOME":
        return Path.home() / ".cache" / "snds-exporter" / default_suffix
    return Path.home() / ".local" / "state" / "snds-exporter" / default_suffix


def _write_secure_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temp_file:
        temp_file.write(content)
        temp_path = Path(temp_file.name)

    os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)
    temp_path.replace(path)


def _write_secure_json(path: Path, payload: dict) -> None:
    _write_secure_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    return json.loads(cache_path.read_text(encoding="utf-8"))


def _save_cache(cache_path: Path, payload: dict) -> None:
    _write_secure_json(cache_path, payload)


def _urlsafe_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _token_expiry_epoch(result: dict) -> int:
    expires_on = result.get("expires_on")
    if expires_on:
        return int(expires_on)

    expires_in = result.get("expires_in")
    if expires_in:
        return int(time.time()) + int(expires_in)

    raise RuntimeError("Microsoft token response did not include an expiry time.")


def _token_error(result: dict | None) -> str:
    if not result:
        return "No token result returned."
    error = result.get("error")
    description = result.get("error_description")
    if error or description:
        return f"{error or 'token_error'}: {description or 'no description'}"
    return "Unknown token acquisition failure."


def _build_auth_url(state: str, code_challenge: str, nonce: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": MANUAL_REDIRECT_URI,
        "scope": " ".join(AUTH_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
        "client_info": "1",
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_ENDPOINT}?{urlencode(params)}"


def _prompt_for_redirect_response(auth_url: str, expected_state: str) -> dict[str, str]:
    logger.info(
        "Open this URL in your local browser and complete the Microsoft sign-in:"
    )
    print(auth_url)
    print()
    logger.info(
        "After login, the browser will redirect to %s and likely show a connection error.",
        MANUAL_REDIRECT_URI,
    )
    logger.info("Copy the full URL from the browser address bar and paste it here.")
    redirect_url = input("Redirect URL: ").strip()
    if not redirect_url:
        raise RuntimeError("No redirect URL was provided.")

    parsed = urlsplit(redirect_url)
    response = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if not response and parsed.fragment:
        response = dict(parse_qsl(parsed.fragment, keep_blank_values=True))

    if "code" not in response:
        raise RuntimeError("The redirect URL did not contain an authorization code.")
    if "state" not in response:
        raise RuntimeError("The redirect URL did not contain a state value.")
    if response["state"] != expected_state:
        raise RuntimeError(
            f"state mismatch: expected {expected_state} but received {response['state']}"
        )
    return response


def _exchange_token(payload: dict) -> dict:
    response = requests.post(
        TOKEN_ENDPOINT,
        data=payload,
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if "access_token" not in result:
        raise RuntimeError(_token_error(result))
    return result


def _store_tokens(token_path: Path, cache_path: Path, result: dict) -> int:
    access_token = result["access_token"].strip()
    expires_at = _token_expiry_epoch(result)
    refresh_token = result.get("refresh_token", "").strip()

    _write_secure_text(token_path, access_token + "\n")
    _save_cache(
        cache_path,
        {
            "access_token_expires_at": expires_at,
            "refresh_token": refresh_token,
        },
    )

    logger.info(
        "Wrote refreshed SNDS access token to %s; expires at %s.",
        token_path,
        time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(expires_at)),
    )
    if not refresh_token:
        logger.warning("Microsoft did not return a refresh token.")
    return expires_at


def _refresh_access_token(cache_path: Path, token_path: Path) -> int | None:
    cache = _load_cache(cache_path)
    refresh_token = cache.get("refresh_token", "").strip()
    if not refresh_token:
        return None

    result = _exchange_token(
        {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(TOKEN_SCOPES),
        }
    )
    return _store_tokens(token_path, cache_path, result)


def _acquire_token_manually(cache_path: Path, token_path: Path) -> int:
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_hex(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _urlsafe_sha256(code_verifier)
    auth_url = _build_auth_url(state, code_challenge, nonce)
    auth_response = _prompt_for_redirect_response(auth_url, state)

    result = _exchange_token(
        {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": auth_response["code"],
            "redirect_uri": MANUAL_REDIRECT_URI,
            "scope": " ".join(TOKEN_SCOPES),
            "code_verifier": code_verifier,
        }
    )
    return _store_tokens(token_path, cache_path, result)


def acquire_access_token(
    cache_path: Path,
    token_path: Path,
    allow_interactive: bool,
) -> int:
    refreshed = _refresh_access_token(cache_path, token_path)
    if refreshed is not None:
        return refreshed
    if not allow_interactive:
        raise RuntimeError("No refresh token available for non-interactive refresh.")
    return _acquire_token_manually(cache_path, token_path)


def run_loop(args: argparse.Namespace) -> int:
    while True:
        try:
            expires_at = acquire_access_token(
                cache_path=args.cache_file,
                token_path=args.token_file,
                allow_interactive=not args.non_interactive,
            )
        except Exception as exc:
            logger.error("Failed to refresh SNDS token: %s", exc)
            if not args.watch:
                return 1
            time.sleep(args.retry_seconds)
            continue

        if not args.watch:
            return 0

        sleep_seconds = max(
            30,
            expires_at - int(time.time()) - args.refresh_before_seconds,
        )
        logger.info("Next refresh attempt in %s seconds.", sleep_seconds)
        time.sleep(sleep_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire and refresh a Microsoft SNDS REST API access token, then "
            "write it to a file for snds-exporter."
        )
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=_xdg_path("XDG_STATE_HOME", "access-token"),
        help="Destination file containing the current bearer token.",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=_xdg_path("XDG_CACHE_HOME", "token-cache.json"),
        help="OAuth cache file containing the current refresh token.",
    )
    parser.add_argument(
        "--refresh-before-seconds",
        type=int,
        default=DEFAULT_REFRESH_BEFORE_SECONDS,
        help="Refresh this many seconds before the current access token expires.",
    )
    parser.add_argument(
        "--retry-seconds",
        type=int,
        default=DEFAULT_RETRY_SECONDS,
        help="Retry delay after a failed refresh while in --watch mode.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep running and refresh the token before it expires.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail instead of requesting a pasted browser redirect URL.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_loop(args)


if __name__ == "__main__":
    sys.exit(main())
