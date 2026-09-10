#!/usr/bin/env python3
"""
YouTube OAuth Setup — Overclocked Tech News (second channel)
==============================================================
Same as setup_youtube_oauth.py but saves to a SEPARATE token file
(youtube_token_tech.json) so this channel's credentials don't overwrite
the original/gaming channel's token. When the browser consent screen
appears, choose the Overclocked Tech News channel/account, not the
original one.

Usage:
  python3 scripts/setup_youtube_oauth_tech.py
"""

import os
import sys
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

SKILL_DIR = Path.home() / ".verticals"
TOKEN_PATH = SKILL_DIR / "youtube_token_tech.json"


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing dependency. Install it with:")
        print("   pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    SKILL_DIR.mkdir(parents=True, exist_ok=True)

    print("YouTube OAuth Setup — Overclocked Tech News")
    print("=" * 50)
    print()
    print("Reuses the same client_secret.json from the original channel setup")
    print("(same Google Cloud project) — just sign in as/select the NEW")
    print("Overclocked Tech News channel when the browser prompts you.")
    print()

    client_secrets = input("Path to your client_secret.json: ").strip()
    client_secrets = str(Path(client_secrets).expanduser())

    if not Path(client_secrets).exists():
        print(f"File not found: {client_secrets}")
        sys.exit(1)

    print("\nOpening browser for Google sign-in...")
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
    creds = flow.run_local_server(port=0)

    fd = os.open(str(TOKEN_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    print(f"\nToken saved to {TOKEN_PATH}")
    print("Ready — reupload_tech_legacy.py will use this token.")


if __name__ == "__main__":
    main()
