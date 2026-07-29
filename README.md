# Skool Sync

Production-ready automation that exports members from your **free** and **paid** Skool communities every day and keeps everything in a **Google Sheet** — including free-to-paid conversion tracking.

## What it does

- Exports member lists from each Skool community via the Apify actor and stores the raw CSVs locally.
- Normalizes member records and writes them to a **Members** sheet.
- Appends a daily metrics row to a **DailyMetrics** sheet.
- Detects members who joined the paid community after being in the free community.
- Uses the Apify actor to export members without local browser automation.
- Runs on your own computer or a cheap VPS, scheduled once per day.

## Quick start for owners (Streamlit wizard)

The easiest way to set up the sync is with the included Streamlit wizard.

```bash
streamlit run scripts/setup_wizard.py
```

Then follow the steps:

1. **Connect Apify** — paste your Apify API token and verify it.
2. **Connect Google Sheets** — enter your Google OAuth Client ID/Secret, click authorize, and verify the target spreadsheet.
3. **Connect Skool** — enter your Skool admin email, password, and the free/paid community URLs.
4. **Review & Save** — the wizard writes your `.env` file and installs a daily cron job.
5. **Run first sync** — click the button to test everything end-to-end.

### Google Cloud prerequisites for the wizard

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **Google Sheets API**.
3. Go to **APIs & Services > Credentials** and create an **OAuth client ID** of type **Desktop app**.
4. Copy the **Client ID** and **Client secret**.
5. If the app is not published, add your Google account under the **Test users** section.

> **Security note:** the wizard stores your Apify token, Google client secret, refresh token, and Skool password in `.env` in plain text. Keep `.env` private and never commit it.

---

## Manual setup

### 1. Choose a Google Sheets auth method

You can authenticate with either:

- **Service account (recommended for automation):** a JSON key file shared with the sheet.
- **OAuth 2.0 client:** `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET`; you authorize once and the app stores a refresh token.

#### Service account (recommended)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or pick an existing one).
3. Navigate to **IAM & Admin > Service accounts** and create a service account.
4. Under **Keys**, add a new JSON key and download the `.json` file.
5. Keep the file safe — you will pass its path to the setup script below.

#### OAuth 2.0 client

1. In the [Google Cloud Console](https://console.cloud.google.com/), go to **APIs & Services > Credentials**.
2. Click **Create credentials > OAuth client ID** and choose **Desktop app** (the `--console` flow requires a Desktop app client).
3. Copy the **Client ID** and **Client secret**.
4. Make sure the **Google Sheets API** is enabled and your Google account is added as a test user if the app is not published.

> **Note:** A **Web application** OAuth client will not work with `python scripts/google_auth.py --console`; use a Desktop app client instead.

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the interactive setup

```bash
python scripts/setup.py
```

It will ask for:
- Skool email and password
- Free and paid community URLs (or slugs)
- Path to your Google service account JSON file
- Google Sheets spreadsheet ID or URL
- Whether to schedule a daily sync

### 3. Authorize access to your Google Sheet

- **If you chose service account:** open `data/credentials.json`, find `client_email`, and share your Google Sheet with that email as an **Editor**.
- **If you chose OAuth client:** run the one-time authorization. This opens a browser:

  ```bash
  python scripts/google_auth.py
  ```

  On a headless server, use the console flow instead:

  ```bash
  python scripts/google_auth.py --console
  ```

  > **Heads-up:** the `--console` flow uses a deprecated Google redirect URI that may not work with newly created OAuth clients. If it fails, run `python scripts/google_auth.py` on a machine with a browser and copy the generated `data/google_oauth_token.json` to your server.

### 4. Test without writing anything

```bash
python -m src.main --dry-run
```

### 5. Run the real sync

```bash
python -m src.main
```

The Google Sheet will now contain a **Members** tab, a **DailyMetrics** tab, and a **SyncRuns** tab.

> **Note:** the sink now updates existing rows in place and appends only new rows, rather than clearing the whole sheet every run. This makes daily syncs much faster and preserves any extra columns you add beyond the standard schema.

## Scheduling

`scripts/setup.py` can install a cron job for you on macOS/Linux. To do it manually, run the generated helper:

```bash
# macOS / Linux
crontab -e
# Add:
# 0 6 * * * cd /path/to/skool-sync && /path/to/.venv/bin/python -m src.main >> /path/to/skool-sync/data/cron.log 2>&1
```

On Windows, use Task Scheduler to run `scripts/run_sync.bat` daily at your preferred time.

## Backfilling from old CSV snapshots

If you already have Skool member CSVs from a previous date, you can import them without running a live export:

```bash
python -m src.main --backfill data/raw/2025-01-01
```

The folder must contain:

- `free.csv`
- `paid.csv`

The folder name (`2025-01-01` in the example above) is used as the snapshot date.

## Project structure

```
skool-sync/
  README.md
  requirements.txt
  .env
  scripts/
    setup.py            # interactive owner setup
    setup_wizard.py     # browser-based setup wizard
    run_sync.sh         # generated one-click runner (macOS/Linux)
    run_sync.bat        # generated one-click runner (Windows)
  src/
    main.py             # entry point
    config.py
    sync_engine.py
    exporters/          # Apify export backend
    sinks/              # Google Sheets sink (default)
    ...
  data/
    raw/                # raw CSV snapshots by date
    reports/            # sync summaries
```

## Running tests

```bash
pytest
```

## Configuration

All configuration is loaded from `.env`. The easiest way to create `.env` is `python scripts/setup.py`.

## Error recovery

- Review `data/reports/sync_summary_*.json` for the latest run details.
- Re-run with `--dry-run` to validate without writes.
- If an Apify run fails, check the Apify console for the actor run log.

## Assumptions and limitations

- You must be an admin of both Skool communities.
- Skool does not expose a public member API, so this tool uses the third-party `cristiantala/skool-all-in-one-api` Apify actor to export members.
- Google Sheets works well for the expected scale (tens of thousands of free members, a few thousand paid members). If you later need a database or dashboard, the sink layer can be extended.
- Apify actor availability and pricing are controlled by Apify and the actor author.

## Nice-to-have roadmap

- Archive old snapshots to cold storage.
- Optional Slack/email notifications on completion or failure.
