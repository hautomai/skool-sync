"""Streamlit setup wizard for the Skool -> Google Sheets sync.

Usage:
    streamlit run scripts/setup_wizard.py

The wizard guides a non-technical owner through:
  1. Verifying their Apify API token.
  2. Connecting Google Sheets via OAuth 2.0.
  3. Entering Skool credentials and community URLs.
  4. Saving a ready-to-use .env file.
  5. Installing a daily cron job (Linux/macOS).
  6. Running the first real sync.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import platform
import shutil
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Add project root to path so we can import src helpers.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from apify_client import ApifyClientAsync
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ENV_PATH = PROJECT_ROOT / ".env"
OAUTH_REDIRECT_PORT = 8085
OAUTH_REDIRECT_URI = f"http://localhost:{OAUTH_REDIRECT_PORT}/oauth2callback"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
OAUTH_TIMEOUT_SECONDS = 300


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------
def _prefill_from_env() -> dict[str, str]:
    """Return a mapping of env keys to values from .env, if any."""
    env = _read_env()
    creds_path = env.get("GOOGLE_SHEETS_CREDENTIALS_PATH", "./data/credentials.json")
    has_sa = bool(creds_path) and Path(creds_path).is_file()
    return {
        "apify_token": env.get("APIFY_API_TOKEN", ""),
        "google_auth_method": env.get("GOOGLE_AUTH_METHOD", "service_account" if has_sa else "oauth"),
        "google_sheets_credentials_path": creds_path,
        "google_client_id": env.get("GOOGLE_CLIENT_ID", ""),
        "google_client_secret": env.get("GOOGLE_CLIENT_SECRET", ""),
        "google_refresh_token": env.get("GOOGLE_REFRESH_TOKEN", ""),
        "spreadsheet_id": env.get("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
        "skool_email": env.get("SKOOL_EMAIL", ""),
        "skool_password": env.get("SKOOL_PASSWORD", ""),
        "free_community_url": env.get("FREE_COMMUNITY_URL", ""),
        "paid_community_url": env.get("PAID_COMMUNITY_URL", ""),
        "members_filter": env.get("GOOGLE_SHEETS_MEMBERS_FILTER", "converted"),
    }


def _init_state() -> None:
    env_defaults = _prefill_from_env()
    defaults = {
        "apify_token": env_defaults["apify_token"],
        "apify_verified": bool(env_defaults["apify_token"]),
        "google_auth_method": env_defaults["google_auth_method"],
        "google_sheets_credentials_path": env_defaults["google_sheets_credentials_path"],
        "google_client_id": env_defaults["google_client_id"],
        "google_client_secret": env_defaults["google_client_secret"],
        "google_refresh_token": env_defaults["google_refresh_token"],
        "google_token_ready": bool(env_defaults["google_refresh_token"]),
        "google_sheet_verified": False,
        "spreadsheet_id": env_defaults["spreadsheet_id"],
        "skool_email": env_defaults["skool_email"],
        "skool_password": env_defaults["skool_password"],
        "free_community_url": env_defaults["free_community_url"],
        "paid_community_url": env_defaults["paid_community_url"],
        "members_filter": env_defaults["members_filter"] if env_defaults["members_filter"] in ("converted", "all") else "converted",
        "schedule_time": "06:00",
        "install_schedule": True,
        "step": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _step() -> int:
    return st.session_state["step"]


def _next_step() -> None:
    st.session_state["step"] += 1


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------
def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def _write_env(config: dict[str, str]) -> None:
    if ENV_PATH.exists():
        backup = ENV_PATH.with_suffix(".env.backup")
        shutil.copy2(ENV_PATH, backup)
        st.info(f"Existing .env backed up to {backup}")

    # Merge with any existing values so we do not clobber unrelated keys.
    existing = _read_env()
    merged = {**existing, **config}

    # Ensure required keys are present even if empty.
    for key in [
        "SKOOL_EMAIL",
        "SKOOL_PASSWORD",
        "FREE_COMMUNITY_URL",
        "PAID_COMMUNITY_URL",
        "GOOGLE_SHEETS_SPREADSHEET_ID",
        "GOOGLE_AUTH_METHOD",
        "GOOGLE_SHEETS_CREDENTIALS_PATH",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REFRESH_TOKEN",
        "GOOGLE_OAUTH_TOKEN_PATH",
        "APIFY_API_TOKEN",
        "APIFY_ACTOR_ID",
        "GOOGLE_SHEETS_MEMBERS_FILTER",
        "TIMEZONE",
        "LOG_LEVEL",
    ]:
        if key not in merged:
            merged[key] = ""

    merged["APIFY_ACTOR_ID"] = merged.get("APIFY_ACTOR_ID") or "cristiantala/skool-all-in-one-api"
    merged["GOOGLE_OAUTH_TOKEN_PATH"] = "./data/google_oauth_token.json"

    # Build a deterministic file for readability.
    lines: list[str] = [
        "# Skool authentication",
        f"SKOOL_EMAIL={merged['SKOOL_EMAIL']}",
        f"SKOOL_PASSWORD={merged['SKOOL_PASSWORD']}",
        "",
        "# Skool communities (full URL or slug)",
        f"FREE_COMMUNITY_URL={merged['FREE_COMMUNITY_URL']}",
        f"PAID_COMMUNITY_URL={merged['PAID_COMMUNITY_URL']}",
        "",
        "# Google Sheets target",
        f"GOOGLE_SHEETS_SPREADSHEET_ID={merged['GOOGLE_SHEETS_SPREADSHEET_ID']}",
        "GOOGLE_SHEETS_MEMBERS_SHEET=Members",
        "GOOGLE_SHEETS_DAILY_METRICS_SHEET=DailyMetrics",
        f"GOOGLE_SHEETS_MEMBERS_FILTER={merged['GOOGLE_SHEETS_MEMBERS_FILTER']}",
        "",
        "# Google Sheets authentication method: 'service_account' or 'oauth'",
        f"GOOGLE_AUTH_METHOD={merged['GOOGLE_AUTH_METHOD']}",
        f"GOOGLE_SHEETS_CREDENTIALS_PATH={merged['GOOGLE_SHEETS_CREDENTIALS_PATH']}",
        "",
        "# Google OAuth credentials (only used when GOOGLE_AUTH_METHOD=oauth)",
        f"GOOGLE_CLIENT_ID={merged['GOOGLE_CLIENT_ID']}",
        f"GOOGLE_CLIENT_SECRET={merged['GOOGLE_CLIENT_SECRET']}",
        f"GOOGLE_REFRESH_TOKEN={merged['GOOGLE_REFRESH_TOKEN']}",
        f"GOOGLE_OAUTH_TOKEN_PATH={merged['GOOGLE_OAUTH_TOKEN_PATH']}",
        "",
        "# Apify configuration",
        f"APIFY_API_TOKEN={merged['APIFY_API_TOKEN']}",
        f"APIFY_ACTOR_ID={merged['APIFY_ACTOR_ID']}",
        "",
        "# Skool cookie caching (auto-managed)",
        "SKOOL_COOKIES_PATH=./data/skool_cookies.json",
        "SKOOL_COOKIES_REFRESH_HOURS=24",
        "",
        "# Operational",
        f"TIMEZONE={merged.get('TIMEZONE', 'UTC')}",
        f"LOG_LEVEL={merged.get('LOG_LEVEL', 'INFO')}",
        "",
    ]
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Apify verification
# ---------------------------------------------------------------------------
async def _verify_apify_token(token: str) -> bool:
    try:
        client = ApifyClientAsync(token)
        await client.user().get()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Google OAuth callback server
# ---------------------------------------------------------------------------
class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/oauth2callback":
            self.send_response(404)
            self.end_headers()
            return

        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code")
            return

        # Store the code on the class for the main thread to pick up.
        _OAuthCallbackHandler.last_code = code  # type: ignore[attr-defined]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Google authorization successful!</h2>"
            b"You can close this tab and return to the setup wizard.</body></html>"
        )

    def log_message(self, format: str, *args: object) -> None:
        # Suppress server noise.
        pass


_OAuthCallbackHandler.last_code = None  # type: ignore[attr-defined]


def _start_oauth_server() -> threading.Thread:
    try:
        server = http.server.HTTPServer(("localhost", OAUTH_REDIRECT_PORT), _OAuthCallbackHandler)
    except OSError as exc:
        raise RuntimeError(f"Port {OAUTH_REDIRECT_PORT} is already in use. Close any other setup tabs and try again.") from exc
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.server = server  # type: ignore[attr-defined]
    thread.start()
    return thread


def _client_config(client_id: str, client_secret: str) -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [OAUTH_REDIRECT_URI],
        }
    }


def _create_flow(client_id: str, client_secret: str) -> Flow:
    """Create a Google OAuth flow for a Desktop app (installed client)."""
    flow = Flow.from_client_config(_client_config(client_id, client_secret), scopes=SCOPES)
    flow.redirect_uri = OAUTH_REDIRECT_URI
    return flow


def _get_authorization_url(flow: Flow) -> str:
    """Generate the authorization URL from an existing flow (keeps PKCE verifier)."""
    auth_url, _ = flow.authorization_url(prompt="consent")
    return auth_url


def _exchange_code_for_credentials(client_id: str, client_secret: str, code: str, code_verifier: str):
    """Exchange the authorization code, restoring the PKCE verifier."""
    flow = _create_flow(client_id, client_secret)
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    return flow.credentials


def _extract_spreadsheet_id(value: str) -> str:
    value = value.strip()
    if "/spreadsheets/d/" in value:
        import re
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", value)
        if match:
            return match.group(1)
    return value


def _verify_google_sheet_access(
    spreadsheet_id: str,
    *,
    client_id: str = "",
    client_secret: str = "",
    refresh_token: str = "",
    service_account_path: str = "",
) -> tuple[bool, str]:
    try:
        if service_account_path:
            from google.oauth2.service_account import Credentials as ServiceAccountCredentials

            creds = ServiceAccountCredentials.from_service_account_file(
                service_account_path, scopes=SCOPES
            )
        else:
            from google.oauth2.credentials import Credentials as OAuthCredentials
            from google.auth.transport.requests import Request

            creds = OAuthCredentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=SCOPES,
            )
            creds.refresh(Request())

        service = build("sheets", "v4", credentials=creds)
        service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        return True, "Spreadsheet accessible"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Cron / scheduling
# ---------------------------------------------------------------------------
def _install_cron(time_str: str) -> tuple[bool, str]:
    import re as re_mod
    match = re_mod.match(r"^(\d{1,2}):(\d{2})$", time_str.strip())
    if not match:
        return False, "Time must be HH:MM"
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return False, "Invalid hour/minute"

    marker = "# skool-sync daily run"
    command = (
        f"{minute} {hour} * * * cd {PROJECT_ROOT} "
        f"&& {sys.executable} -m src.main >> {PROJECT_ROOT}/data/cron.log 2>&1"
    )

    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, check=False
        )
        existing = result.stdout if result.returncode == 0 else ""
        if marker in existing:
            return True, "Cron job already exists."
        new_crontab = existing.rstrip() + f"\n{marker}\n{command}\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
        return True, f"Installed daily cron job at {time_str}"
    except FileNotFoundError:
        return False, "crontab not found. Please schedule manually."
    except subprocess.CalledProcessError as exc:
        return False, f"Cron install failed: {exc}"


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
def _render_header() -> None:
    st.title("🎓 Skool Sync Setup Wizard")
    st.markdown(
        "Follow these steps to connect your Skool communities, Apify account, and Google Sheet."
    )
    progress = min((_step() + 1) / 5, 1.0)
    st.progress(progress, text=f"Step {_step() + 1} of 5")


def _step_apify() -> None:
    st.header("1. Connect Apify")
    st.markdown(
        "You need an Apify API token. Get it from "
        "[Apify Console > Integrations](https://console.apify.com/account/integrations)."
    )

    token = st.text_input(
        "Apify API token",
        value=st.session_state["apify_token"],
        type="password",
        key="apify_token_input",
    )
    st.session_state["apify_token"] = token

    if st.button("Verify Apify token"):
        if not token:
            st.warning("Please enter your Apify API token.")
            return
        with st.spinner("Verifying token..."):
            ok = asyncio.run(_verify_apify_token(token))
        if ok:
            st.session_state["apify_verified"] = True
            st.success("Apify token verified!")
        else:
            st.error("Invalid Apify token or network error.")

    if st.session_state["apify_verified"]:
        if st.button("Next", type="primary"):
            _next_step()
            st.rerun()


def _step_google() -> None:
    st.header("2. Connect Google Sheets")

    auth_method = st.radio(
        "Authentication method",
        options=["service_account", "oauth"],
        index=0 if st.session_state["google_auth_method"] == "service_account" else 1,
        format_func=lambda x: "Service account (recommended)" if x == "service_account" else "OAuth 2.0",
        key="google_auth_method_input",
        help=(
            "Service account is recommended for automation: the JSON key stays valid "
            "until the key is deleted, so there are no refresh tokens that can expire. "
            "OAuth 2.0 is easier for a quick test, but the refresh token may expire, "
            "especially for unpublished Google Cloud apps."
        ),
    )
    st.session_state["google_auth_method"] = auth_method

    st.info(
        "**Tip:** Service accounts are simpler for daily automation because they avoid "
        "OAuth refresh-token expiry. With OAuth, unpublished apps can lose access after "
        "~7 days and require re-authorization."
    )

    client_id = ""
    client_secret = ""
    service_account_path = ""

    if auth_method == "service_account":
        st.markdown(
            "Upload the service-account JSON key from Google Cloud, then share your "
            "spreadsheet with the service-account email as an **Editor**."
        )
        uploaded = st.file_uploader(
            "Service account JSON key",
            type=["json"],
            key="service_account_json_uploader",
        )
        if uploaded is not None:
            try:
                sa_info = json.loads(uploaded.getvalue())
                sa_email = sa_info.get("client_email", "")
                sa_private_key = sa_info.get("private_key", "")
                sa_type = sa_info.get("type", "")
                if sa_type != "service_account" or not sa_email or not sa_private_key:
                    st.error("The uploaded file does not look like a valid Google service-account key (missing type='service_account', client_email, or private_key).")
                    sa_info = {}
                    uploaded = None  # type: ignore[assignment]
                if uploaded is not None:
                    data_dir = PROJECT_ROOT / "data"
                    data_dir.mkdir(parents=True, exist_ok=True)
                    creds_path = data_dir / "credentials.json"
                    creds_path.write_bytes(uploaded.getvalue())
                    st.session_state["google_sheets_credentials_path"] = str(creds_path)
                    service_account_path = str(creds_path)
                    st.success(f"Service account JSON saved. Share the sheet with: `{sa_email}`")
            except json.JSONDecodeError:
                st.error("The uploaded file is not valid JSON.")

        existing_path = Path(st.session_state["google_sheets_credentials_path"])
        if not service_account_path and existing_path.is_file():
            service_account_path = str(existing_path)
            st.caption(f"Using existing service account key: `{existing_path}`")
    else:
        st.markdown(
            "Create a Google OAuth 2.0 client (Desktop app) in Google Cloud, "
            "enable the Google Sheets API, and paste the credentials below."
        )
        # If a service-account key file exists from a previous setup, warn that it
        # will still be used unless GOOGLE_AUTH_METHOD is set to oauth (which it
        # will be after saving) or the file is removed.
        existing_sa = Path(st.session_state["google_sheets_credentials_path"])
        if existing_sa.is_file():
            st.warning(
                f"A service-account key still exists at `{existing_sa}`. "
                "Because you selected OAuth, it will be ignored after you save. "
                "If you ever switch back to service account, this file will be reused."
            )
        client_id = st.text_input(
            "Google Client ID",
            value=st.session_state["google_client_id"],
            key="google_client_id_input",
        )
        client_secret = st.text_input(
            "Google Client Secret",
            value=st.session_state["google_client_secret"],
            type="password",
            key="google_client_secret_input",
        )
        st.session_state["google_client_id"] = client_id
        st.session_state["google_client_secret"] = client_secret

        if not st.session_state["google_token_ready"]:
            if st.button("Authorize Google"):
                if not (client_id and client_secret):
                    st.warning("Enter both Client ID and Client Secret first.")
                else:
                    # Start local callback server.
                    server_thread = _start_oauth_server()

                    # Create a single Flow instance and store its PKCE code verifier.
                    # We only keep the verifier (not the whole Flow) because Streamlit
                    # session_state may not serialize arbitrary objects reliably.
                    flow = _create_flow(client_id, client_secret)
                    auth_url = _get_authorization_url(flow)
                    st.session_state["google_code_verifier"] = flow.code_verifier

                    st.info("A browser tab will open for Google authorization. Return here when done.")
                    webbrowser.open(auth_url)

                    # Poll for the OAuth code from the callback server.
                    import time as _time
                    oauth_start = _time.monotonic()
                    with st.spinner("Waiting for Google authorization..."):
                        while _OAuthCallbackHandler.last_code is None:
                            if _time.monotonic() - oauth_start > OAUTH_TIMEOUT_SECONDS:
                                server_thread.server.shutdown()  # type: ignore[attr-defined]
                                st.error("Google authorization timed out after 5 minutes. Please try again.")
                                return
                            _time.sleep(0.5)

                    code = _OAuthCallbackHandler.last_code
                    _OAuthCallbackHandler.last_code = None
                    server_thread.server.shutdown()  # type: ignore[attr-defined]

                    try:
                        code_verifier = st.session_state.get("google_code_verifier")
                        if not code_verifier:
                            st.error("OAuth code verifier was lost. Please try authorizing again.")
                            return
                        creds = _exchange_code_for_credentials(client_id, client_secret, code, code_verifier)
                        refresh_token = creds.refresh_token or ""
                        if not refresh_token:
                            st.error("Google did not return a refresh token. Make sure you checked 'prompt=consent' and are not re-using an existing authorization without it.")
                            return
                        st.session_state["google_token_ready"] = True
                        st.session_state["google_refresh_token"] = refresh_token
                        # Clear the verifier now that we have the refresh token.
                        st.session_state.pop("google_code_verifier", None)
                        st.success("Google Sheets authorized!")
                    except Exception as exc:
                        st.error(f"OAuth failed: {exc}")
        else:
            st.success("Google Sheets is connected via OAuth.")

    spreadsheet_input = st.text_input(
        "Google Sheet URL or Spreadsheet ID",
        value=st.session_state["spreadsheet_id"],
        key="spreadsheet_id_input",
    )
    st.session_state["spreadsheet_id"] = _extract_spreadsheet_id(spreadsheet_input)

    if st.session_state["spreadsheet_id"]:
        ready = bool(service_account_path) or st.session_state["google_token_ready"]
        if ready and st.button("Verify sheet access"):
            if auth_method == "service_account":
                ok, msg = _verify_google_sheet_access(
                    st.session_state["spreadsheet_id"],
                    service_account_path=service_account_path,
                )
            else:
                ok, msg = _verify_google_sheet_access(
                    st.session_state["spreadsheet_id"],
                    client_id=st.session_state["google_client_id"],
                    client_secret=st.session_state["google_client_secret"],
                    refresh_token=st.session_state["google_refresh_token"],
                )
            if ok:
                st.session_state["google_sheet_verified"] = True
                st.success("Google Sheet is accessible!")
            else:
                st.session_state["google_sheet_verified"] = False
                st.error(f"Cannot access the spreadsheet: {msg}")
                if auth_method == "service_account":
                    st.info("Make sure the spreadsheet is shared with the service-account email as an Editor and that the Google Sheets API is enabled.")
                else:
                    st.info("Make sure the Google Sheets API is enabled and your Google account is added as a test user in Google Cloud.")

        next_clicked = st.button("Next", type="primary")
        if next_clicked and not st.session_state["google_sheet_verified"]:
            st.warning("Please verify sheet access before continuing.")
        elif next_clicked:
            _next_step()
            st.rerun()


def _step_skool() -> None:
    st.header("3. Connect Skool")
    st.markdown(
        "Enter the Skool admin account that can access both communities. "
        "We recommend creating a dedicated admin account for this sync."
    )

    skool_email = st.text_input(
        "Skool admin email", value=st.session_state["skool_email"], key="skool_email_input"
    )
    skool_password = st.text_input(
        "Skool admin password",
        value=st.session_state["skool_password"],
        type="password",
        key="skool_password_input",
    )
    free_url = st.text_input(
        "Free community URL or slug",
        value=st.session_state["free_community_url"],
        key="free_url_input",
    )
    paid_url = st.text_input(
        "Paid community URL or slug",
        value=st.session_state["paid_community_url"],
        key="paid_url_input",
    )

    st.session_state["skool_email"] = skool_email
    st.session_state["skool_password"] = skool_password
    st.session_state["free_community_url"] = free_url
    st.session_state["paid_community_url"] = paid_url

    members_filter = st.selectbox(
        "Members sheet should show",
        options=["converted", "all"],
        index=0 if st.session_state["members_filter"] == "converted" else 1,
        key="members_filter_input",
    )
    st.session_state["members_filter"] = members_filter

    missing: list[str] = []
    if not skool_email or "@" not in skool_email:
        missing.append("a valid Skool email")
    if not skool_password:
        missing.append("a Skool password")
    if not free_url:
        missing.append("a free community URL")
    if not paid_url:
        missing.append("a paid community URL")

    if missing:
        st.warning("Please provide: " + ", ".join(missing))
    else:
        if st.button("Next", type="primary"):
            _next_step()
            st.rerun()


def _step_review() -> None:
    st.header("4. Review & Save")
    st.markdown("Make sure everything looks correct, then save your configuration.")

    st.subheader("Configuration")
    st.write(f"**Apify token:** {'*' * 8}{st.session_state['apify_token'][-4:] if st.session_state['apify_token'] else ''}")
    st.write(f"**Google Sheet ID:** {st.session_state['spreadsheet_id']}")
    st.write(f"**Skool email:** {st.session_state['skool_email']}")
    st.write(f"**Free community:** {st.session_state['free_community_url']}")
    st.write(f"**Paid community:** {st.session_state['paid_community_url']}")
    st.write(f"**Members filter:** {st.session_state['members_filter']}")

    if st.button("Save .env", type="primary"):
        is_service_account = st.session_state["google_auth_method"] == "service_account"
        config: dict[str, str] = {
            "APIFY_API_TOKEN": st.session_state["apify_token"],
            "SKOOL_EMAIL": st.session_state["skool_email"],
            "SKOOL_PASSWORD": st.session_state["skool_password"],
            "FREE_COMMUNITY_URL": st.session_state["free_community_url"],
            "PAID_COMMUNITY_URL": st.session_state["paid_community_url"],
            "GOOGLE_SHEETS_SPREADSHEET_ID": st.session_state["spreadsheet_id"],
            "GOOGLE_AUTH_METHOD": st.session_state["google_auth_method"],
            "GOOGLE_SHEETS_CREDENTIALS_PATH": st.session_state["google_sheets_credentials_path"] if is_service_account else "",
            "GOOGLE_CLIENT_ID": st.session_state["google_client_id"] if not is_service_account else "",
            "GOOGLE_CLIENT_SECRET": st.session_state["google_client_secret"] if not is_service_account else "",
            "GOOGLE_REFRESH_TOKEN": st.session_state.get("google_refresh_token", ""),
            "GOOGLE_OAUTH_TOKEN_PATH": "./data/google_oauth_token.json",
            "GOOGLE_SHEETS_MEMBERS_FILTER": st.session_state["members_filter"],
            "TIMEZONE": "UTC",
            "LOG_LEVEL": "INFO",
        }
        _write_env(config)
        st.success(f"Saved configuration to {ENV_PATH}")

        if st.session_state["install_schedule"]:
            system = platform.system()
            if system in ("Darwin", "Linux"):
                ok, msg = _install_cron(st.session_state["schedule_time"])
                if ok:
                    st.success(msg)
                else:
                    st.warning(msg)
            else:
                st.info("Automatic scheduling is only supported on macOS/Linux. Use Task Scheduler on Windows.")

        _next_step()
        st.rerun()


def _step_run() -> None:
    st.header("5. Run your first sync")
    st.markdown("Everything is configured. Run the sync now to verify it works.")

    if st.button("Run first sync now"):
        with st.spinner("Running sync... this may take a minute"):
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "src.main", "--log-level", "INFO"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=300,
                )
                st.text_area("Sync output", value=proc.stdout + "\n" + proc.stderr, height=300)
                if proc.returncode == 0:
                    st.success("First sync completed successfully!")
                else:
                    st.error(f"Sync failed with exit code {proc.returncode}")
            except subprocess.TimeoutExpired:
                st.error("Sync timed out after 5 minutes.")
            except Exception as exc:
                st.error(f"Error running sync: {exc}")

    st.markdown("---")
    st.markdown(
        "You can close this wizard now. The sync is scheduled to run daily at "
        f"**{st.session_state['schedule_time']}** (if cron was installed)."
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main() -> None:
    _init_state()
    _render_header()

    steps = [_step_apify, _step_google, _step_skool, _step_review, _step_run]
    current_step = min(_step(), len(steps) - 1)
    steps[current_step]()


if __name__ == "__main__":
    main()
