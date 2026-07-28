# Skool → Airtable Sync — Revised Plan

## User decisions
1. **Export backend**: Playwright now, with a pluggable backend interface so an Apify actor can be swapped in later.
2. **Auth**: Try `storage_state` (saved session) first, fall back to email/password login.
3. **Sink**: Prefer simple. Either Google Sheets **or** one Airtable table with all member information.
4. **Departed members**: Soft-delete / archive only; do not create new snapshot rows for removed members.
5. **Notifications**: Local JSON report only; n8n can forward it elsewhere if needed.

## Proposed architecture
- `src/exporters/base.py` — abstract `SkoolExporter` interface.
- `src/exporters/playwright_exporter.py` — primary implementation.
- `src/exporters/apify_exporter.py` — stub/adapter for later.
- `src/sinks/base.py` — abstract `Sink` interface.
- `src/sinks/airtable_sink.py` — single-table design with history columns.
- `src/sinks/google_sheets_sink.py` — optional alternative sink.
- `src/sync_engine.py` — orchestrates export → normalize → diff → sink.
- `src/conversion_detector.py` — marks free→paid conversions.
- `src/reporter.py` — JSON summary + readable output.

## Airtable single-table design
`Members` table fields:
- email (primary, lowercase)
- full_name, first_name, last_name
- free_status / paid_status (active/removed)
- free_joined_at / paid_joined_at
- first_seen_free_at / first_seen_paid_at
- conversion_detected_at
- current_status (free_only | paid_only | both | converted)
- free_source_file / paid_source_file
- membership_answers (JSON)
- last_synced_at
- snapshot_dates (JSON array of dates present in each community)

A lightweight `SyncRuns` table tracks run metadata.

## Risks / questions for thinker
- Is a single Airtable table sufficient for "daily membership changes over time"?
- How should the Google Sheets sink behave when the sheet has >50k rows?
- Should the conversion detector run before or after the sink write?
- What is the minimal viable Airtable field set to satisfy all required outputs?
