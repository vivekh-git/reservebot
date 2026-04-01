# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a reservation sniper bot — a Playwright-based Python script that automatically logs into a website, checks for available booking slots, and books them when found. It runs on a daily schedule via GitHub Actions.

## Running Locally

```bash
# Install dependencies
pip install playwright
playwright install chromium

# Run the bot (requires credentials in environment)
MY_USER=<your_username> MY_PASS=<your_password> python sniper.py
```

## GitHub Actions Deployment

The bot runs automatically via `.github/workflows/daily_check.yaml`:
- **Schedule:** Daily at 8:00 AM UTC
- **Manual trigger:** `workflow_dispatch` (run manually from GitHub Actions UI)
- **Required secrets:** Set `MY_USER` and `MY_PASS` in the repository's GitHub Secrets

## Architecture

The entire logic lives in `sniper.py` as a single `run_sniper()` function:

1. **Login** — navigates to the target site and fills credentials from `MY_USER`/`MY_PASS` env vars
2. **Availability check** — looks for a CSS selector (`.available-slot`) on the reservations page
3. **Booking** — clicks `.book-button` if a slot is found

## Key Customization Points

Before deploying, these placeholders in `sniper.py` must be updated:
- `"https://website.com"` (appears twice) → actual login URL and reservations page URL
- `.available-slot` → actual CSS selector for an available slot on the target site
- `.book-button` → actual CSS selector for the booking button
- `headless=False` → change to `headless=True` for GitHub Actions (no display available)
- Remove or guard `page.pause()` (line 27) — it blocks execution and is for local debugging only
- Fix typo on line 1: `iimport` → `import`

## Notification Hook

The comment `# Add a simple notification here (like a Telegram alert)` on the booking success path is an intentional extension point for alerting when a slot is booked.
