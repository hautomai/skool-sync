"""Obtain and save a Google OAuth 2.0 refresh token for unattended sync runs.

Usage:
    python scripts/google_auth.py
    python scripts/google_auth.py --console   # for headless servers

Prerequisites:
    - GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env
      (or in the environment).
    - The OAuth client ID must be created as a **Desktop app** type in
      Google Cloud (required for the --console flow).
    - The OAuth consent screen in Google Cloud must have the
      Google Sheets API enabled and the app added as a test user.

The script will save the token to GOOGLE_OAUTH_TOKEN_PATH (default
./data/google_oauth_token.json). After that, src.main can run without
browser interaction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

try:
    from src.config import Settings
except ImportError:
    # Allow running directly from the project root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.config import Settings


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _client_config(client_id: str, client_secret: str) -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost", "urn:ietf:wg:oauth:2.0:oob"],
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize Google Sheets access")
    parser.add_argument(
        "--console",
        action="store_true",
        help="Use console-based flow instead of opening a browser (for headless servers).",
    )
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    settings = Settings()

    client_id = settings.google_client_id
    client_secret = settings.google_client_secret
    if not client_id or not client_secret:
        print("ERROR: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    token_path = Path(settings.google_oauth_token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    flow = InstalledAppFlow.from_client_config(
        _client_config(client_id, client_secret), SCOPES
    )

    if args.console:
        creds = flow.run_console()
    else:
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Token saved to {token_path}")
    print("You can now run the sync. On a server, copy this token file to the same path.")


if __name__ == "__main__":
    main()
