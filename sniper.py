import os
import time
import multiprocessing
import argparse
import requests
from datetime import date, timedelta
from playwright.sync_api import sync_playwright

NOTIFY_EMAIL = "vivekh@gmail.com"
SENDGRID_FROM = "vivekh@gmail.com"

def send_email(subject, body):
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        print("[email] SENDGRID_API_KEY not set, skipping email.")
        return
    response = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "personalizations": [{"to": [{"email": NOTIFY_EMAIL}]}],
            "from": {"email": SENDGRID_FROM},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        },
    )
    if response.status_code == 202:
        print(f"[email] Sent: {subject}")
    else:
        print(f"[email] Failed to send: {response.status_code} {response.text}")

PREFERRED_TIMES = ['5:00', '4:00', '3:30']
SLOT_SELECTOR = '.date_option.available'

def find_preferred_time_slot(page, user):
    print(f"  [{user}] -> querying time slots with selector: '.slot.highlight'")
    available = page.query_selector_all('.slot.highlight')
    print(f"  [{user}] -> found {len(available)} bookable time slot(s)")
    for slot in available:
        time_el = slot.query_selector('.time')
        text = time_el.inner_text().strip() if time_el else 'unknown'
        classes = slot.get_attribute('class')
        print(f"  [{user}]    time slot: '{text}' classes='{classes}'")

    for preferred in PREFERRED_TIMES:
        print(f"  [{user}] -> checking preferred time: '{preferred}'")
        for slot in available:
            time_el = slot.query_selector('.time')
            if time_el and preferred in time_el.inner_text():
                print(f"  [{user}] -> matched preferred time '{preferred}'")
                return slot
    if available:
        time_el = available[0].query_selector('.time')
        fallback = time_el.inner_text().strip() if time_el else 'unknown'
        print(f"  [{user}] -> no preferred time matched, falling back to first available: '{fallback}'")
        return available[0]
    print(f"  [{user}] -> no bookable time slots found")
    return None

def run_sniper(user, password, target_date):
    with sync_playwright() as p:
        headless = os.environ.get("CI") == "true"
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        # 1. Login
        print(f"[{user}] --- STEP 1: Login ---")
        print(f"[{user}] Navigating to: https://reservations.mountmadonna.org/visitors/login")
        page.goto("https://reservations.mountmadonna.org/visitors/login")
        print(f"[{user}] Waiting for selector: 'input[name=\"visitor[email]\"]'")
        page.wait_for_selector('input[name="visitor[email]"]')
        print(f"[{user}] Filling email field...")
        page.fill('input[name="visitor[email]"]', user)
        print(f"[{user}] Filling password field...")
        page.fill('input[name="visitor[password]"]', password)
        print(f"[{user}] Clicking: 'input[type=\"submit\"][value=\"Log in\"]'")
        page.click('input[type="submit"][value="Log in"]')
        page.wait_for_load_state('load')
        print(f"[{user}] After login, URL: {page.url}")
        if page.url == "https://reservations.mountmadonna.org/visitors/login":
            print(f"[{user}] Login failed — still on login page. Exiting.")
            browser.close()
            return
        print(f"[{user}] Login successful.")

        # 2. Confirm information intermediate page
        print(f"[{user}] --- STEP 2: Navigate to reservations ---")
        print(f"[{user}] Navigating to: https://reservations.mountmadonna.org/reservations/new")
        page.goto("https://reservations.mountmadonna.org/reservations/new")
        print(f"[{user}] Landed at: {page.url}")
        if page.url == "https://reservations.mountmadonna.org/visitors/confirm_information":
            print(f"[{user}] Intermediate confirm_information page — clicking Continue...")
            page.click('input[type="submit"][value="Continue"]')
            page.wait_for_url("**/reservations/new", timeout=10000)
            print(f"[{user}] After Continue, URL: {page.url}")

        # 3. Poll for available slot
        print(f"[{user}] --- STEP 3: Polling for {target_date} ---")
        print(f"[{user}] Using date slot selector: '{SLOT_SELECTOR}'")
        is_ci = os.environ.get("CI") == "true"
        max_polls = 180 if is_ci else 3
        print(f"[{user}] Max polls: {max_polls} ({'CI' if is_ci else 'local'} mode)")
        booked = False
        for i in range(max_polls):
            print(f"[{user}] Poll {i+1}/{max_polls} — URL: {page.url} — {'CI' if is_ci else 'local'} mode")
            print(f"[{user}] Poll {i+1}: waiting for date grid to render (.date_option)...")
            page.wait_for_selector('.date_option', timeout=10000)
            all_date_options = page.query_selector_all('.date_option')
            print(f"[{user}] Poll {i+1}: all date_option elements ({len(all_date_options)} total):")
            for d in all_date_options:
                print(f"  classes='{d.get_attribute('class')}' data-date='{d.get_attribute('data-date')}' status='{d.get_attribute('data-detail-status')}'")
            slots = page.query_selector_all(SLOT_SELECTOR)
            print(f"[{user}] Poll {i+1}: found {len(slots)} available date slot(s) matching '{SLOT_SELECTOR}'")
            for slot in slots:
                day = slot.query_selector('.date_day')
                date = slot.get_attribute('data-date')
                status = slot.get_attribute('data-detail-status')
                day_text = day.inner_text().strip() if day else 'unknown'
                classes = slot.get_attribute('class')
                print(f"  [{user}] date slot: day='{day_text}' date='{date}' status='{status}' classes='{classes}'")
                if date == target_date:
                    print(f"  [{user}] -> TARGET MATCHED: {day_text} {date} — clicking...")
                    slot.click()
                    page.wait_for_load_state('load')
                    print(f"  [{user}] -> After date click, URL: {page.url}")

                    time_slot = find_preferred_time_slot(page, user)
                    if time_slot:
                        time_el = time_slot.query_selector('.time')
                        time_text = time_el.inner_text().strip() if time_el else 'unknown'
                        print(f"  [{user}] -> Clicking time slot: '{time_text}'")
                        time_slot.click()

                        print(f"  [{user}] -> Waiting for confirmation page selector: '#card-button'")
                        page.wait_for_selector('#card-button')
                        print(f"  [{user}] -> URL: {page.url} — clicking Confirm (#card-button)...")
                        page.click('#card-button')

                        print(f"  [{user}] -> Waiting for parking selector: '.price_option.no_contribution'")
                        page.wait_for_selector('.price_option.no_contribution')
                        print(f"  [{user}] -> URL: {page.url} — selecting No Charge parking...")
                        page.click('.price_option.no_contribution')

                        print(f"  [{user}] -> Waiting for final Confirm selector: '#card-button'")
                        page.wait_for_selector('#card-button')
                        print(f"  [{user}] -> URL: {page.url} — clicking final Confirm (#card-button)...")
                        page.click('#card-button')
                        page.wait_for_load_state('load')
                        print(f"  [{user}] -> Final URL: {page.url}")

                        if page.url == "https://reservations.mountmadonna.org/reservations/confirmation":
                            print(f"  [{user}] -> BOOKING CONFIRMED!")
                            send_email(
                                f"Reservation booked for {user}",
                                f"Successfully booked Tuesday April 7, 2026 at {time_text} for {user}."
                            )
                            booked = True
                        else:
                            print(f"  [{user}] -> ERROR: unexpected page after final confirm: {page.url}")
                            send_email(
                                f"Reservation attempt failed for {user}",
                                f"Booking did not complete. Ended up at: {page.url}"
                            )
                    else:
                        print(f"  [{user}] -> no bookable time slots on {target_date}, skipping")
                    break
            if booked:
                break
            print(f"[{user}] Poll {i+1}: no target slot yet, waiting 1s then reloading...")
            time.sleep(1)
            page.reload()

        if not booked:
            print(f"[{user}] Polling complete — no booking made.")
            send_email(
                f"No reservation found for {user}",
                f"Polled {max_polls} time(s) for {target_date} — no available slots found."
            )

        print(f"[{user}] Closing browser.")
        browser.close()

def next_target_date():
    today = date.today()
    if today.weekday() in (5, 6):  # 5 = Saturday, 6 = Sunday
        return str(today)
    days_ahead = (5 - today.weekday()) % 7  # next Saturday
    return str(today + timedelta(days=days_ahead))

def load_target_date(args):
    if args.date:
        print(f"[config] Using date from --date argument: {args.date}")
        return args.date
    computed = next_target_date()
    print(f"[config] No date provided — defaulting to: {computed}")
    return computed

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reservation sniper bot")
    parser.add_argument("--date", help="Target date to book (YYYY-MM-DD), e.g. 2026-04-07")
    args = parser.parse_args()

    target_date = load_target_date(args)

    credentials = []
    i = 1
    while True:
        user = os.environ.get(f"MY_USER_{i}")
        password = os.environ.get(f"MY_PASS_{i}")
        if not user or not password:
            break
        credentials.append((user, password))
        i += 1

    if not credentials:
        print("No credentials found. Set MY_USER_1/MY_PASS_1, MY_USER_2/MY_PASS_2, etc.")
        exit(1)

    print(f"Running with {len(credentials)} account(s) in parallel...")
    processes = [multiprocessing.Process(target=run_sniper, args=(u, pw, target_date)) for u, pw in credentials]
    for p in processes:
        p.start()
    for p in processes:
        p.join()
