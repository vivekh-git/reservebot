import os
import time
import multiprocessing
from playwright.sync_api import sync_playwright

PREFERRED_TIMES = ['4:00', '3:30']
SLOT_SELECTOR = '.date_option:not(.check_back):not(.closed):not(.unavailable)'

def find_preferred_time_slot(page):
    available = page.query_selector_all('.time_row:not(.none_left)')
    for preferred in PREFERRED_TIMES:
        for slot in available:
            time_el = slot.query_selector('.time')
            if time_el and preferred in time_el.inner_text():
                return slot
    return available[0] if available else None

def run_sniper(user, password):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # 1. Login
        print(f"[{user}] Navigating to login page...")
        page.goto("https://reservations.mountmadonna.org/visitors/login")
        print(f"[{user}] Waiting for login form...")
        page.wait_for_selector('input[name="visitor[email]"]')
        print(f"[{user}] Filling credentials...")
        page.fill('input[name="visitor[email]"]', user)
        page.fill('input[name="visitor[password]"]', password)
        print(f"[{user}] Clicking login button...")
        page.click('input[type="submit"][value="Log in"]')
        page.wait_for_load_state('networkidle')
        print(f"[{user}] Logged in, now at: {page.url}")
        if page.url == "https://reservations.mountmadonna.org/visitors/login":
            print(f"[{user}] Login failed — check credentials. Exiting.")
            browser.close()
            return

        # 2. Confirm information intermediate page
        page.goto("https://reservations.mountmadonna.org/reservations/new")
        if page.url == "https://reservations.mountmadonna.org/visitors/confirm_information":
            page.click('input[type="submit"][value="Continue"]')
        page.pause()  # TEMP: verify we landed on reservations page correctly

        # 3. Poll for available Saturday/Sunday slot (up to 5 minutes)
        print(f"[{user}] Polling for available slots...")
        booked = False
        for i in range(300):
            print(f"[{user}] Poll {i+1}: looking for '{SLOT_SELECTOR}' on {page.url}")
            slots = page.query_selector_all(SLOT_SELECTOR)
            print(f"[{user}] Poll {i+1}: found {len(slots)} available slot(s)")
            for slot in slots:
                day = slot.query_selector('.date_day')
                date = slot.get_attribute('data-date')
                day_text = day.inner_text().strip() if day else 'unknown'
                print(f"  [{user}] -> slot: {day_text} {date}")
                if day_text in ('Saturday', 'Sunday'):
                    print(f"  [{user}] -> clicking date: {day_text} {date}")
                    slot.click()
                    time_slot = find_preferred_time_slot(page)
                    if time_slot:
                        time_el = time_slot.query_selector('.time')
                        time_text = time_el.inner_text().strip() if time_el else 'unknown'
                        print(f"  [{user}] -> clicking time slot: {time_text}")
                        time_slot.click()
                        page.pause()  # TEMP: inspect what appears after clicking a time slot
                    else:
                        print(f"  [{user}] -> no available time slots on this date")
                    booked = True
                    break
            if booked:
                break
            time.sleep(1)
            page.reload()

        if not booked:
            print(f"[{user}] No availability found.")

        browser.close()

if __name__ == "__main__":
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
    processes = [multiprocessing.Process(target=run_sniper, args=(u, pw)) for u, pw in credentials]
    for p in processes:
        p.start()
    for p in processes:
        p.join()
