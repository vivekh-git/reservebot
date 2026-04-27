import os
import re
import time
import json
import multiprocessing
import argparse
import yaml
import requests
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

NOTIFY_EMAIL = "vivekh@gmail.com"
SENDGRID_FROM = "vivekh@gmail.com"

STEP_SUCCESS = "success"
STEP_FAILURE = "failure"
STEP_DRY_RUN = "dry_run"
STEP_NOT_FOUND = "not_found"
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
    days_ahead = (5 - today.weekday()) % 7  # 0 if today is Saturday
    return str(today + timedelta(days=days_ahead))


def ts():
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


def resolve(value, ctx):
    if not isinstance(value, str):
        return value
    return value.format(**ctx)


def parse_release_time(status, timezone_str="America/Los_Angeles"):
    """Parse release time from status text like 'Next release today @ 9:00am'.
    Returns a timezone-aware datetime for today at that time, or None if not parseable."""
    m = re.search(r'@\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm))', status, re.IGNORECASE)
    if not m:
        return None
    time_str = m.group(1).replace(" ", "").lower()
    fmt = "%I:%M%p" if ":" in time_str else "%I%p"
    tz = ZoneInfo(timezone_str)
    now = datetime.now(tz)
    t = datetime.strptime(time_str, fmt)
    return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


def execute_step(page, step, ctx):
    action = step["action"]
    label = ctx["user"]
    print(f"  [{label}] action: {action}")

    if action == "navigate":
        url = resolve(step["url"], ctx)
        print(f"  [{label}] [{ts()}] -> {url}")
        page.goto(url)

    elif action == "wait_for":
        selector = step["selector"]
        timeout = step.get("timeout", 30000)
        print(f"  [{label}] [{ts()}] -> waiting for: '{selector}' (timeout: {timeout}ms)")
        page.wait_for_selector(selector, timeout=timeout)
        print(f"  [{label}] [{ts()}] -> found: '{selector}'")

    elif action == "wait_for_load":
        print(f"  [{label}] [{ts()}] -> waiting for page load")
        page.wait_for_load_state("load")
        print(f"  [{label}] [{ts()}] -> URL: {page.url}")

    elif action == "wait_for_url":
        pattern = resolve(step["url"], ctx)
        timeout = step.get("timeout", 30000)
        print(f"  [{label}] [{ts()}] -> waiting for URL: '{pattern}' (timeout: {timeout}ms)")
        page.wait_for_url(pattern, timeout=timeout)
        print(f"  [{label}] [{ts()}] -> URL: {page.url}")

    elif action == "fill":
        selector = step["selector"]
        raw_value = step["value"]
        value = resolve(raw_value, ctx)
        masked = "***" if "{password}" in raw_value else value
        print(f"  [{label}] [{ts()}] -> filling '{selector}' with '{masked}'")
        page.fill(selector, value)

    elif action == "click":
        selector = step["selector"]
        print(f"  [{label}] [{ts()}] -> clicking '{selector}'")
        page.click(selector)
        print(f"  [{label}] [{ts()}] -> clicked '{selector}'")

    elif action == "assert_url_not":
        url = resolve(step["url"], ctx)
        print(f"  [{label}] [{ts()}] -> assert not on '{url}' (current: {page.url})")
        if page.url == url:
            print(f"  [{label}] [{ts()}] -> FAILURE: {step.get('error', 'Unexpected URL')}")
            return STEP_FAILURE

    elif action == "assert_url":
        url = resolve(step["url"], ctx)
        print(f"  [{label}] [{ts()}] -> assert on '{url}' (current: {page.url})")
        if page.url == url or page.url.startswith(url):
            print(f"  [{label}] [{ts()}] -> {step.get('success', 'URL confirmed')}")
            return STEP_SUCCESS
        else:
            print(f"  [{label}] [{ts()}] -> FAILURE: expected '{url}', got '{page.url}'")
            return STEP_FAILURE

    elif action == "check":
        selector = step["selector"]
        print(f"  [{label}] [{ts()}] -> checking checkbox: '{selector}'")
        page.check(selector)

    elif action == "select":
        selector = step["selector"]
        value = resolve(step["value"], ctx)
        print(f"  [{label}] [{ts()}] -> selecting '{value}' in '{selector}'")
        page.select_option(selector, label=value)

    elif action == "extract":
        selector = step["selector"]
        text_selector = step.get("text_selector")
        key = step.get("key", "extracted_data")
        elements = page.query_selector_all(selector)
        texts = []
        for el in elements:
            text_el = el.query_selector(text_selector) if text_selector else el
            text = text_el.inner_text().strip() if text_el else ""
            if text:
                texts.append(text)
        ctx[key] = texts
        ctx[key + "_text"] = "\n".join(texts) if texts else "None found"
        print(f"  [{label}] [{ts()}] extracted {len(texts)} item(s) for '{key}': {texts}")

    elif action == "email_report":
        subject = resolve(step["subject"], ctx)
        body = resolve(step["body"], ctx)
        print(f"  [{label}] [{ts()}] -> sending report email: '{subject}'")
        send_email(subject, body)
        ctx["email_sent"] = True
        return STEP_CONTINUE

    elif action == "scroll_to_bottom":
        selector = step["selector"]
        print(f"  [{label}] [{ts()}] -> scrolling to bottom of '{selector}'")
        page.eval_on_selector(selector, "el => el.scrollTop = el.scrollHeight")

    elif action == "pause":
        print(f"  [{label}] [{ts()}] -> pausing for manual inspection")
        page.pause()

    elif action == "if_on_url":
        url = resolve(step["url"], ctx)
        print(f"  [{label}] [{ts()}] -> if_on_url '{url}' (current: {page.url})")
        if page.url == url:
            print(f"  [{label}] [{ts()}] -> matched, running sub-steps...")
            return execute_steps(page, step["then"], ctx)

    elif action == "poll":
        return execute_poll(page, step, ctx)

    elif action == "click_preferred":
        return execute_click_preferred(page, step, ctx)

    else:
        print(f"  [{label}] [{ts()}] -> unknown action: {action}")

    return STEP_CONTINUE


def execute_steps(page, steps, ctx):
    is_ci = os.environ.get("CI") == "true"
    no_pause = ctx.get("no_pause", False)
    for step in steps:
        label = ctx["user"]
        print(f"\n[{label}] ── step: {step['action']} [{ts()}] ──────────────────────────────")
        result = execute_step(page, step, ctx)
        print(f"[{label}] URL after step: {page.url}")
        if not is_ci and not no_pause and step.get("action") not in ("pause", "wait_for", "wait_for_load", "wait_for_url"):
            print(f"[{label}] [local] pausing for inspection...")
            page.pause()
        if result is not STEP_CONTINUE:
            return result
    return STEP_CONTINUE


def execute_poll(page, step, ctx):
    label = ctx["user"]
    debug = ctx.get("debug", False)
    timezone_str = ctx.get("timezone", "America/Los_Angeles")
    precise_reload = step.get("precise_reload", False)
    slot_retries = step.get("slot_retries", 1)
    print(f"  [{label}] [{ts()}] poll: landed on reservations page")
    selector = step["selector"]
    match_attr = step.get("match_attribute")
    match_value = resolve(step.get("match_value", ""), ctx)
    on_match_steps = step.get("on_match", [])

    if match_attr and match_value:
        targeted = f"{selector}[{match_attr}='{match_value}']"
    else:
        targeted = selector

    wait_ms = 30000
    max_reloads = 24

    print(f"  [{label}] [{ts()}] poll: waiting for '{targeted}' (precise_reload={precise_reload}, slot_retries={slot_retries})")

    for attempt in range(max_reloads + 1):
        # Log current date grid state
        try:
            page.wait_for_selector(".date_option", timeout=10000)
            all_options = page.query_selector_all(".date_option")
            print(f"  [{label}] [{ts()}] Attempt {attempt+1} — date grid ({len(all_options)} options):")
            for d in all_options:
                print(f"    classes='{d.get_attribute('class')}' date='{d.get_attribute('data-date')}' status='{d.get_attribute('data-detail-status')}'")
        except Exception:
            print(f"  [{label}] [{ts()}] Attempt {attempt+1} — date grid did not render")

        # Stop early if target date is unavailable or check_back on a future date
        if match_value:
            try:
                target_el = page.query_selector(f".date_option[data-date='{match_value}']")
                if target_el:
                    classes = target_el.get_attribute("class") or ""
                    status = target_el.get_attribute("data-detail-status") or ""
                    if "unavailable" in classes:
                        print(f"  [{label}] [{ts()}] target date {match_value} is unavailable — stopping")
                        return STEP_NOT_FOUND
                    if "check_back" in classes and "release today" not in status.lower():
                        print(f"  [{label}] [{ts()}] target date {match_value} releases on a future date — stopping")
                        return STEP_NOT_FOUND
            except Exception:
                pass

        # Precise reload: if target date is check_back with a known release time, sleep until then
        if precise_reload:
            try:
                target_el = page.query_selector(f".date_option[data-date='{match_value}']")
                if target_el:
                    status = target_el.get_attribute("data-detail-status") or ""
                    if "release today" in status.lower():
                        release_dt = parse_release_time(status, timezone_str)
                        if release_dt:
                            tz = ZoneInfo(timezone_str)
                            sleep_secs = (release_dt - datetime.now(tz)).total_seconds()
                            if sleep_secs > 0:
                                print(f"  [{label}] [{ts()}] date is check_back — sleeping {sleep_secs:.1f}s until release at {release_dt.strftime('%H:%M:%S %Z')}")
                                time.sleep(sleep_secs)
                                print(f"  [{label}] [{ts()}] reloading at release time")
                                page.reload()
                                continue
            except Exception as e:
                print(f"  [{label}] [{ts()}] precise_reload error: {e}")

        # Inject MutationObserver to log class changes
        try:
            page.evaluate("""
                () => {
                    if (window.__domObserver) window.__domObserver.disconnect();
                    const observer = new MutationObserver(mutations => {
                        for (const m of mutations) {
                            if (m.attributeName === 'class') {
                                const el = m.target;
                                console.log('[dom:classchange] date=' + el.getAttribute('data-date') +
                                    ' class="' + el.className + '" at ' + new Date().toISOString());
                            }
                        }
                    });
                    document.querySelectorAll('.date_option').forEach(el =>
                        observer.observe(el, { attributes: true, attributeFilter: ['class'] })
                    );
                    window.__domObserver = observer;
                    console.log('[dom:observer] watching ' + document.querySelectorAll('.date_option').length +
                        ' date_option elements at ' + new Date().toISOString());
                }
            """)
        except Exception as e:
            print(f"  [{label}] [{ts()}] [dom:observer] failed to inject: {e}")

        # In debug mode, extend timeout to 2 minutes past release time to observe Ably
        if debug:
            try:
                target_el = page.query_selector(f".date_option[data-date='{match_value}']")
                status = (target_el.get_attribute("data-detail-status") or "") if target_el else ""
                if "release today" in status.lower():
                    release_dt = parse_release_time(status, timezone_str)
                    if release_dt:
                        tz = ZoneInfo(timezone_str)
                        debug_until = release_dt.replace(minute=release_dt.minute + 2)
                        wait_ms = max(wait_ms, int((debug_until - datetime.now(tz)).total_seconds() * 1000))
                        print(f"  [{label}] [{ts()}] [debug] extended timeout to {wait_ms}ms (until {debug_until.strftime('%H:%M:%S %Z')})")
            except Exception as e:
                print(f"  [{label}] [{ts()}] [debug] timeout extension error: {e}")

        try:
            print(f"  [{label}] [{ts()}] waiting for '{targeted}'...")
            element = page.wait_for_selector(targeted, timeout=wait_ms)
            print(f"  [{label}] [{ts()}] TARGET APPEARED — clicking immediately")
            element.click()
            page.wait_for_load_state("load")
            print(f"  [{label}] [{ts()}] after date click, URL: {page.url}")

            # In debug mode, stop before booking — just log what we see
            if debug:
                print(f"  [{label}] [{ts()}] [debug] stopping before booking steps")
                return STEP_DRY_RUN

            # Split on_match at card_button_selector so execute_poll owns the full booking lifecycle
            card_button_selector = step.get("card_button_selector")
            if card_button_selector:
                split_idx = next(
                    (i for i, s in enumerate(on_match_steps)
                     if s.get("action") == "wait_for" and s.get("selector") == card_button_selector),
                    None
                )
                slot_steps = on_match_steps[:split_idx] if split_idx is not None else on_match_steps
                booking_steps = on_match_steps[split_idx + 1:] if split_idx is not None else []
            else:
                slot_steps = None  # fall through to legacy path

            for slot_attempt in range(slot_retries):
                slot_selected = False
                try:
                    if slot_steps is not None:
                        # Phase 1: select slot (re-clickable on failure)
                        result = execute_steps(page, slot_steps, ctx)
                        if result is not STEP_CONTINUE:
                            return result
                        slot_selected = True
                        # Phase 2: wait for server to resolve — no arbitrary timeout
                        # Success: card_button_selector becomes visible. Failure: target date becomes unavailable.
                        print(f"  [{label}] [{ts()}] slot clicked — waiting for '{card_button_selector}' or date unavailable (no timeout)")
                        page.wait_for_function(
                            f"() => {{"
                            f"  var el = document.querySelector('{card_button_selector}');"
                            f"  return (!!el && el.offsetWidth > 0) ||"
                            f"         !!document.querySelector('.date_option.unavailable[data-date=\"{match_value}\"]');"
                            f"}}",
                            timeout=0
                        )
                        card_el = page.query_selector(card_button_selector)
                        if card_el and card_el.is_visible():
                            print(f"  [{label}] [{ts()}] '{card_button_selector}' appeared — completing booking")
                            return execute_steps(page, booking_steps, ctx)
                        else:
                            print(f"  [{label}] [{ts()}] date became unavailable — slot was lost")
                            raise Exception(f"date became unavailable without {card_button_selector} appearing")
                    else:
                        # Legacy: run all on_match steps as before
                        return execute_steps(page, on_match_steps, ctx)
                except Exception as e:
                    if not slot_selected and slot_attempt < slot_retries - 1 and "/reservations/new" in page.url:
                        # Only re-click when slot selection failed — not when server already has our request
                        print(f"  [{label}] [{ts()}] slot load failed (attempt {slot_attempt+1}/{slot_retries}), re-clicking date: {e}")
                        el = page.query_selector(targeted)
                        if el:
                            el.click()
                            page.wait_for_load_state("load")
                            print(f"  [{label}] [{ts()}] re-clicked date, URL: {page.url}")
                    else:
                        raise
        except Exception as e:
            print(f"  [{label}] [{ts()}] Attempt {attempt+1} timed out or failed: {e}")
            if attempt < max_reloads:
                print(f"  [{label}] [{ts()}] reloading page and retrying...")
                page.reload()
                time.sleep(1)

    print(f"  [{label}] [{ts()}] poll exhausted after {max_reloads + 1} attempts — no target found")
    return STEP_NOT_FOUND


def execute_click_preferred(page, step, ctx):
    label = ctx["user"]
    selector = step["selector"]
    text_selector = step.get("text_selector", "")
    preferred_list = step.get("preferred", [])
    strict = step.get("strict", False)

    all_slots = page.query_selector_all(".slot_container")
    pref_avail = pref_unavail = nonpref_avail = nonpref_unavail = 0
    for slot in all_slots:
        text_el = slot.query_selector(text_selector) if text_selector else None
        text = text_el.inner_text().strip() if text_el else "?"
        inner = slot.query_selector(".slot")
        inner_classes = inner.get_attribute("class") if inner else ""
        is_unavailable = "unavailable" in (inner_classes or "")
        is_preferred = any(p in text for p in preferred_list)
        status = "unavailable" if is_unavailable else "available"
        pref_label = "preferred" if is_preferred else "non-preferred"
        print(f"    slot: '{text}' {pref_label} {status} classes='{inner_classes}'")
        if is_preferred and is_unavailable:     pref_unavail += 1
        elif is_preferred:                      pref_avail += 1
        elif is_unavailable:                    nonpref_unavail += 1
        else:                                   nonpref_avail += 1
    print(f"  [{label}] [{ts()}] preferred: {pref_avail} available, {pref_unavail} unavailable | non-preferred: {nonpref_avail} available, {nonpref_unavail} unavailable")

    available = page.query_selector_all(selector)

    for preferred in preferred_list:
        print(f"  [{label}] checking preferred: '{preferred}'")
        for slot in available:
            text_el = slot.query_selector(text_selector) if text_selector else None
            if text_el and preferred in text_el.inner_text():
                text = text_el.inner_text().strip()
                if ctx.get("dry_run"):
                    print(f"  [{label}] DRY RUN: would click preferred '{text}' — stopping before booking")
                    ctx["booked_time"] = text
                    return STEP_DRY_RUN
                print(f"  [{label}] clicking preferred: '{text}'")
                slot.click()
                ctx["booked_time"] = text
                return STEP_CONTINUE

    if available:
        if strict:
            print(f"  [{label}] no preferred match — strict mode, skipping non-preferred slots")
            return STEP_NOT_FOUND
        text_el = available[0].query_selector(text_selector) if text_selector else None
        text = text_el.inner_text().strip() if text_el else "unknown"
        if ctx.get("dry_run"):
            print(f"  [{label}] DRY RUN: would click first available '{text}' — stopping before booking")
            ctx["booked_time"] = text
            return STEP_DRY_RUN
        print(f"  [{label}] no preferred match, clicking first available: '{text}'")
        available[0].click()
        ctx["booked_time"] = text
        return STEP_CONTINUE

    print(f"  [{label}] no bookable time slots found")
    return STEP_FAILURE


def run_site(site_config, user, password, target_date, dry_run=False, no_pause=False, debug=False):
    name = site_config["name"]
    is_ci = os.environ.get("CI") == "true"
    timezone = site_config.get("timezone", "America/Los_Angeles")
    ctx = {
        "user": user,
        "password": password,
        "target_date": target_date,
        "booked_time": "unknown",
        "dry_run": dry_run,
        "no_pause": no_pause,
        "debug": debug,
        "timezone": timezone,
    }

    print(f"[{user}] Starting '{name}' — target_date: {target_date}" + (" [DEBUG MODE]" if debug else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=is_ci)
        context = browser.new_context()
        page = context.new_page()

        page.on("console", lambda msg: print(f"  [{user}] [{ts()}] [browser:{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"  [{user}] [{ts()}] [browser:pageerror] {err}"))

        def on_response(response):
            if debug:
                print(f"  [{user}] [{ts()}] [http:{response.status}] {response.url}")
            elif response.status in (429, 503):
                print(f"  [{user}] [{ts()}] [http:{response.status}] {response.url}")

        def on_request(request):
            if debug:
                print(f"  [{user}] [{ts()}] [req:{request.method}] {request.url}")

        # Register on context to capture responses from all frames including cross-origin iframes
        context.on("response", on_response)
        context.on("request", on_request)

        def on_websocket(ws):
            url = ws.url
            print(f"  [{user}] [{ts()}] [ws:open] {url}")
            ws.on("close", lambda ws: print(f"  [{user}] [{ts()}] [ws:close] {url}"))

            def on_frame(frame):
                try:
                    payload = frame.get("payload", frame) if isinstance(frame, dict) else frame
                    if isinstance(payload, bytes):
                        return  # skip binary/MessagePack frames
                    data = json.loads(payload)
                    if debug:
                        print(f"  [{user}] [{ts()}] [ws:frame] {json.dumps(data)}")
                        return
                    action = data.get("action")
                    _ABLY_ACTIONS = {4: "CONNECTED", 9: "ERROR", 11: "ATTACHED", 15: "MESSAGE"}
                    if action in _ABLY_ACTIONS:
                        channel = data.get("channel", "")
                        ch_str = f" channel={channel}" if channel else ""
                        print(f"  [{user}] [{ts()}] [ws:ably:{_ABLY_ACTIONS[action]}]{ch_str}")
                except Exception:
                    pass

            ws.on("framereceived", on_frame)

        page.on("websocket", on_websocket)

        result = execute_steps(page, site_config["steps"], ctx)
        browser.close()

    if not ctx.get("email_sent"):
        if result == STEP_DRY_RUN:
            print(f"[{user}] DRY RUN complete — would have booked {target_date} at {ctx['booked_time']} on {name}. No booking made.")
        elif result == STEP_SUCCESS:
            send_email(
                f"Reservation booked — {name} ({user})",
                f"Successfully booked {target_date} at {ctx['booked_time']} for {user} on {name}.",
            )
        elif result == STEP_NOT_FOUND:
            send_email(
                f"No slot available — {name} ({user})",
                f"Ran workflow for {target_date} on {name} — no available slots found for {user}.",
            )
        elif result == STEP_FAILURE:
            send_email(
                f"Workflow failed — {name} ({user})",
                f"Workflow for {target_date} on {name} encountered an error for {user}.",
            )


def load_target_date(args):
    if args.date:
        print(f"[config] Using date from --date argument: {args.date}")
        return args.date
    computed = next_target_date()
    print(f"[config] No date provided — defaulting to: {computed}")
    return computed


if __name__ == "__main__":
    _here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Generic reservation sniper bot")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD)")
    parser.add_argument("--config", default=os.path.join(_here, "sites.yaml"), help="Path to sites config (default: sites.yaml next to sniper.py)")
    parser.add_argument("--dry-run", action="store_true", help="Find available slot and log what would be booked, but do not complete the booking")
    parser.add_argument("--no-pause", action="store_true", help="Skip between-step pauses in local mode (browser still opens visibly)")
    parser.add_argument("--debug", action="store_true", help="Extended logging of all requests, responses, and WS payloads. Waits 2min past release time. Does not book.")
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
                p = multiprocessing.Process(target=run_site, args=(site, user, password, target_date, args.dry_run, args.no_pause, args.debug))
                processes.append(p)

    if not processes:
        print("No credentials found. Check sites.yaml credential env var names.")
        exit(1)

    print(f"Running {len(processes)} account(s) across {len(config['sites'])} site(s) in parallel...")
    for p in processes:
        p.start()
    for p in processes:
        p.join()
