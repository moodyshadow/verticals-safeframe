#!/usr/bin/env python3
"""
TikTok OAuth Setup
==================
Run once to authorize the Content Posting API against your own TikTok
account. Opens a browser for TikTok login, catches the redirect on a local
server, and saves the token to ~/.verticals/tiktok_token.json.

Prerequisites:
  1. Create an app at https://developers.tiktok.com
  2. Add the Content Posting API product
  3. Set the app's redirect URI to http://localhost:8080/callback
  4. Add your own TikTok account as a target/test user (sandbox mode)
  5. Save TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET in ~/.verticals/config.json
     (or as environment variables) before running this script

Usage:
  python scripts/setup_tiktok_oauth.py
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from verticals.config import get_tiktok_credentials, write_secret_file, SKILL_DIR  # noqa: E402

REDIRECT_URI = "http://localhost:8080/callback"
# video.publish IS a real scope, but it's review-gated: it only becomes
# grantable once the app's Content Posting API "Direct Post" toggle is on
# AND (for a Production app) the app has passed TikTok's review. The
# original "client_key... Something went wrong" error traced back to the
# app having zero products/scopes configured at all, not to this scope name
# specifically. Sandbox apps expose video.publish immediately (no review
# needed, restricted to authorized target/test accounts), which is what
# this script targets pre-approval — see docs/tiktok_sandbox_setup notes.
SCOPES = "user.info.basic,video.publish"
TOKEN_PATH = SKILL_DIR / "tiktok_token.json"

_result = {}


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _result["code"] = params.get("code", [None])[0]
        _result["state"] = params.get("state", [None])[0]
        _result["error"] = params.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if _result["error"]:
            body = f"<h2>Authorization failed: {_result['error']}</h2>You can close this tab."
        else:
            body = "<h2>Authorized.</h2>You can close this tab and return to the terminal."
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):
        pass  # silence default request logging


def _make_pkce_pair() -> tuple[str, str]:
    # RFC 7636 specifies code_challenge = BASE64URL(SHA256(verifier)) for
    # the S256 method, but TikTok's Login Kit for Desktop documents its own
    # deviation: code_challenge is a HEX-encoded SHA256 digest instead,
    # while code_challenge_method is still declared as "S256". (PKCE only
    # applies to Desktop/iOS/Android in TikTok's docs — Web relies on the
    # state token + a server-side client secret instead.) A spec-correct
    # base64url challenge was rejected on every attempt with "Code verifier
    # or code challenge is invalid" until switching to hex, which matches
    # TikTok's documented Desktop behavior.
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode("ascii")
    challenge = hashlib.sha256(verifier.encode("ascii")).digest().hex()
    return verifier, challenge


def main():
    import argparse
    import requests

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sandbox", action="store_true",
        help="Use the Sandbox app's client key/secret instead of Production. "
             "Required pre-approval — Production apps can't complete OAuth "
             "until they pass TikTok's review.",
    )
    args = parser.parse_args()

    client_key, client_secret = get_tiktok_credentials(sandbox=args.sandbox)
    prefix = "TIKTOK_SANDBOX_CLIENT" if args.sandbox else "TIKTOK_CLIENT"
    if not client_key or not client_secret:
        print(f"Missing {prefix}_KEY / {prefix}_SECRET.")
        print("Set them in ~/.verticals/config.json or as environment variables.")
        sys.exit(1)

    state = secrets.token_urlsafe(16)
    verifier, challenge = _make_pkce_pair()

    auth_params = {
        "client_key": client_key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(auth_params)

    server = http.server.HTTPServer(("localhost", 8080), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    # Deliberately not auto-opening a browser here — print the URL and let
    # it be opened exactly once, in one browser, to keep the flow simple to
    # follow manually.
    print("Visit this URL to sign in and authorize:")
    print(auth_url)

    thread.join(timeout=180)
    server.server_close()

    if not _result.get("code"):
        print(f"\nAuthorization failed or timed out: {_result.get('error', 'no code received')}")
        sys.exit(1)
    if _result.get("state") != state:
        print("\nState mismatch — possible CSRF, aborting.")
        sys.exit(1)

    print("Exchanging code for tokens...")
    token_resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": _result["code"],
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    if token_resp.status_code != 200:
        print(f"Token exchange failed: {token_resp.status_code} {token_resp.text[:500]}")
        sys.exit(1)

    token_data = token_resp.json()
    if "access_token" not in token_data:
        print(f"No access_token in response: {token_data}")
        sys.exit(1)

    # expires_in is relative (seconds from now) — store an absolute timestamp
    # too so later refresh logic doesn't need to guess when the token was issued.
    import time
    token_data["obtained_at"] = time.time()
    # Record which credential set issued this token so a later refresh uses
    # the matching client_key/secret — a sandbox-issued refresh_token is
    # invalid against production credentials and vice versa.
    token_data["sandbox"] = args.sandbox

    write_secret_file(TOKEN_PATH, json.dumps(token_data, indent=2))
    print(f"\nToken saved to {TOKEN_PATH}")
    print(f"open_id: {token_data.get('open_id', '?')}")
    print(f"scope: {token_data.get('scope', '?')}")
    print("\nYou're set up. The pipeline can now publish to this TikTok account.")


if __name__ == "__main__":
    main()
