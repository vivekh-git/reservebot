import os
import time
import multiprocessing
import argparse
import yaml
import requests
from datetime import date, timedelta
from playwright.sync_api import sync_playwright

NOTIFY_EMAIL = "vivekh@gmail.com"
SENDGRID_FROM = "vivekh@gmail.com"

STEP_SUCCESS = "success"
STEP_FAILURE = "failure"
STEP_CONTINUE = None


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


def next_target_date():
    today = date.today()
    if today.weekday() in (5, 6):  # Saturday=5, Sunday=6
        return str(today)
    days_ahead = (5 - today.weekday()) % 7  # next Saturday
    return str(today + timedelta(days=days_ahead))


def resolve(value, ctx):
    if not isinstance(value, str):
        return value
    return value.format(**ctx)


def execute_step(page, step, ctx):
    action = step["action"]
    label = ctx["user"]
    print(f"  [{label}] action: {action}")

    if action == "navigate":
        url = resolve(step["url"], ctx)
        print(f"  [{label}] -> {url}")
        page.goto(url)

    elif action == "wait_for":
        selector = step["selector"]
        print(f"  [{label}] -> waiting for: '{selector}'")
        page.wait_for_selector(selector)

    elif action == "wait_for_load":
        print(f"  [{label}] -> waiting for page load")
        page.wait_for_load_state("load")
        print(f"  [{label}] -> URL: {page.url}")

    elif action == "wait_for_url":
        pattern = resolve(step["url"], ctx)
        print(f"  [{label}] -> waiting for URL: '{pattern}'")
        page.wait_for_url(pattern, timeout=10000)
        print(f"  [{label}] -> URL: {page.url}")

    elif action == "fill":
        selector = step["selector"]
        raw_value = step["value"]
        value = resolve(raw_value, ctx)
        masked = "***" if "{password}" in raw_value else value
        print(f"  [{label}] -> filling '{selector}' with '{masked}'")
        page.fill(selector, value)

    elif action == "click":
        selector = step["selector"]
        print(f"  [{label}] -> clicking '{selector}'")
        page.click(selector)

    elif action == "assert_url_not":
        url = resolve(step["url"], ctx)
        print(f"  [{label}] -> assert not on '{url}' (current: {page.url})")
        if page.url == url:
            print(f"  [{label}] -> FAILURE: {step.get('error', 'Unexpected URL')}")
            return STEP_FAILURE

    elif action == "assert_url":
        url = resolve(step["url"], ctx)
        print(f"  [{label}] -> assert on '{url}' (current: {page.url})")
        if page.url == url:
            print(f"  [{label}] -> {step.get('success', 'URL confirmed')}")
            return STEP_SUCCESS
        else:
            print(f"  [{label}] -> FAILURE: expected '{url}', got '{page.url}'")
            return STEP_FAILURE

    elif action == "if_on_url":
        url = resolve(step["url"], ctx)
        print(f"  [{label}] -> if_on_url '{url}' (current: {page.url})")
        if page.url == url:
            print(f"  [{label}] -> matched, running sub-steps...")
            return execute_steps(page, step["then"], ctx)

    elif action == "poll":
        return execute_poll(page, step, ctx)

    elif action == "click_preferred":
        return execute_click_preferred(page, step, ctx)

    else:
        print(f"  [{label}] -> unknown action: {action}")

    return STEP_CONTINUE


def execute_steps(page, steps, ctx):
    for step in steps:
        result = execute_step(page, step, ctx)
        if result is not STEP_CONTINUE:
            return result
    return STEP_CONTINUE


def execute_poll(page, step, ctx):
    label = ctx["user"]
    is_ci = os.environ.get("CI") == "true"
    max_polls = 180 if is_ci else 3
    selector = step["selector"]
    match_attr = step.get("match_attribute")
    match_value = resolve(step.get("match_value", ""), ctx)
    on_match_steps = step.get("on_match", [])

    print(f"  [{label}] poll: selector='{selector}' {match_attr}='{match_value}' max_polls={max_polls} ({'CI' if is_ci else 'local'})")

    for i in range(max_polls):
        print(f"  [{label}] Poll {i+1}/{max_polls} — URL: {page.url}")

        try:
            page.wait_for_selector(".date_option", timeout=10000)
        except Exception:
            print(f"  [{label}] date grid did not render, retrying...")
            time.sleep(1)
            page.reload()
            continue

        all_options = page.query_selector_all(".date_option")
        print(f"  [{label}] date_option elements ({len(all_options)} total):")
        for d in all_options:
            print(f"    classes='{d.get_attribute('class')}' date='{d.get_attribute('data-date')}' status='{d.get_attribute('data-detail-status')}'")

        slots = page.query_selector_all(selector)
        print(f"  [{label}] found {len(slots)} matching '{selector}'")

        for slot in slots:
            attr_val = slot.get_attribute(match_attr) if match_attr else None
            if match_attr and attr_val != match_value:
                continue

            print(f"  [{label}] TARGET MATCHED: {match_attr}='{attr_val}' — clicking...")
            slot.click()
            page.wait_for_load_state("load")
            print(f"  [{label}] after date click, URL: {page.url}")

            return execute_steps(page, on_match_steps, ctx)

        print(f"  [{label}] no match, waiting 1s then reloading...")
        time.sleep(1)
        page.reload()

    print(f"  [{label}] poll exhausted — no target found")
    return STEP_CONTINUE


def execute_click_preferred(page, step, ctx):
    label = ctx["user"]
    selector = step["selector"]
    text_selector = step.get("text_selector", "")
    preferred_list = step.get("preferred", [])

    available = page.query_selector_all(selector)
    print(f"  [{label}] click_preferred: {len(available)} slot(s) matching '{selector}'")
    for slot in available:
        text_el = slot.query_selector(text_selector) if text_selector else None
        text = text_el.inner_text().strip() if text_el else "?"
        print(f"    slot: '{text}' classes='{slot.get_attribute('class')}'")

    for preferred in preferred_list:
        print(f"  [{label}] checking preferred: '{preferred}'")
        for slot in available:
            text_el = slot.query_selector(text_selector) if text_selector else None
            if text_el and preferred in text_el.inner_text():
                text = text_el.inner_text().strip()
                print(f"  [{label}] clicking preferred: '{text}'")
                slot.click()
                ctx["booked_time"] = text
                return STEP_CONTINUE

    if available:
        text_el = available[0].query_selector(text_selector) if text_selector else None
        text = text_el.inner_text().strip() if text_el else "unknown"
        print(f"  [{label}] no preferred match, clicking first available: '{text}'")
        available[0].click()
        ctx["booked_time"] = text
        return STEP_CONTINUE

    print(f"  [{label}] no bookable time slots found")
    return STEP_FAILURE


def run_site(site_config, user, password, target_date):
    name = site_config["name"]
    is_ci = os.environ.get("CI") == "true"
    ctx = {
        "user": user,
        "password": password,
        "target_date": target_date,
        "booked_time": "unknown",
    }

    print(f"[{user}] Starting '{name}' — target_date: {target_date}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=is_ci)
        page = browser.new_context().new_page()
        result = execute_steps(page, site_config["steps"], ctx)
        browser.close()

    if result == STEP_SUCCESS:
        send_email(
            f"Reservation booked — {name} ({user})",
            f"Successfully booked {target_date} at {ctx['booked_time']} for {user} on {name}.",
        )
    else:
        send_email(
            f"No reservation found — {name} ({user})",
            f"Ran workflow for {target_date} on {name} — no available slots found for {user}.",
        )


def load_target_date(args):
    if args.date:
        print(f"[config] Using date from --date argument: {args.date}")
        return args.date
    computed = next_target_date()
    print(f"[config] No date provided — defaulting to: {computed}")
    return computed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generic reservation sniper bot")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD)")
    parser.add_argument("--config", default="sites.yaml", help="Path to sites config (default: sites.yaml)")
    args = parser.parse_args()

    target_date = load_target_date(args)

    with open(args.config) as f:
        config = yaml.safe_load(f)

    processes = []
    for site in config["sites"]:
        for cred in site.get("credentials", []):
            user = os.environ.get(cred["user_env"])
            password = os.environ.get(cred["pass_env"])
            if user and password:
                p = multiprocessing.Process(target=run_site, args=(site, user, password, target_date))
                processes.append(p)

    if not processes:
        print("No credentials found. Check sites.yaml credential env var names.")
        exit(1)

    print(f"Running {len(processes)} account(s) across {len(config['sites'])} site(s) in parallel...")
    for p in processes:
        p.start()
    for p in processes:
        p.join()
