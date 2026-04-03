"""
Captures live HTML from the reservations page for selector analysis.
Saves HTML before and after clicking a date option.

Usage:
    MY_USER_1=<email> MY_PASS_1=<password> python capture_html.py
"""
import os
import time
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://reservations.mountmadonna.org/visitors/login"
RESERVATIONS_URL = "https://reservations.mountmadonna.org/reservations/new"
CONFIRM_URL = "https://reservations.mountmadonna.org/visitors/confirm_information"

user = os.environ.get("MY_USER_1")
password = os.environ.get("MY_PASS_1")

if not user or not password:
    print("Set MY_USER_1 and MY_PASS_1 env vars")
    exit(1)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_context().new_page()

    # Login
    print("Logging in...")
    page.goto(LOGIN_URL)
    page.wait_for_selector('input[name="visitor[email]"]')
    page.fill('input[name="visitor[email]"]', user)
    page.fill('input[name="visitor[password]"]', password)
    page.click('input[type="submit"][value="Log in"]')
    page.wait_for_load_state("load")

    # Handle confirm_information screen
    if page.url == CONFIRM_URL:
        print("Handling confirm_information screen...")
        page.click('input[type="submit"][value="Continue"]')
        page.wait_for_url("**/reservations/new")

    page.goto(RESERVATIONS_URL)
    page.wait_for_selector(".date_option")
    print(f"On reservations page: {page.url}")

    # Save HTML before clicking any date
    html_before = page.content()
    with open("html_before_click.html", "w") as f:
        f.write(html_before)
    print("Saved: html_before_click.html")

    # Print all date options
    options = page.query_selector_all(".date_option")
    print(f"\nDate options ({len(options)} total):")
    for opt in options:
        print(f"  class='{opt.get_attribute('class')}' date='{opt.get_attribute('data-date')}' status='{opt.get_attribute('data-detail-status')}'")

    # Click the first non-closed date option (available or check_back)
    target = None
    for opt in options:
        cls = opt.get_attribute("class") or ""
        if "closed" not in cls:
            target = opt
            break

    if target:
        date_val = target.get_attribute("data-date")
        cls = target.get_attribute("class")
        print(f"\nClicking date: {date_val} (class='{cls}')")
        target.click()

        # Wait for AJAX to settle — try waiting for .slot or just time
        print("Waiting for AJAX response...")
        try:
            page.wait_for_selector(".slot", timeout=5000)
            print("  .slot appeared")
        except Exception:
            print("  .slot did not appear within 5s, capturing anyway")
            time.sleep(2)

        html_after = page.content()
        with open("html_after_click.html", "w") as f:
            f.write(html_after)
        print("Saved: html_after_click.html")

        # Print what slot-related elements are visible
        for selector in [".slot", ".slot.highlight", ".time", "#slots", ".time_slot", ".booking-slot"]:
            els = page.query_selector_all(selector)
            if els:
                print(f"\n  '{selector}' — {len(els)} element(s) found:")
                for el in els[:5]:
                    print(f"    class='{el.get_attribute('class')}' text='{el.inner_text().strip()[:80]}'")
    else:
        print("No clickable date found — all dates are closed")

    browser.close()
    print("\nDone. Run: python capture_html.py, then share html_before_click.html and html_after_click.html")
