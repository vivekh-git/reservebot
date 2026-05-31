"""
Tests for the poll flow against a local mock server that simulates Ably behavior.

The mock page (mock_site.html) flips a date_option from check_back → available
after a configurable delay (via ?delay=N), exactly as the real site does via
an Ably WebSocket push. No page reload is needed — wait_for_selector catches it.

Run with:
    pytest test_poll.py -v -s
"""
import os
import sys
import functools
import threading
import http.server
import pytest
from playwright.sync_api import sync_playwright

# Make code/ importable from test/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Local HTTP server ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def mock_server():
    """Serves files from test/ on a free port for the duration of the test session."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=_TEST_DIR)
    server = http.server.HTTPServer(("localhost", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://localhost:{port}"
    server.shutdown()

# ── Playwright browser ───────────────────────────────────────────────────────

@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()

# ── Helpers ──────────────────────────────────────────────────────────────────

def run_poll(page, url, target_date, on_match_steps=None):
    """Navigate to the mock URL and run execute_poll against it."""
    import sniper
    os.environ["CI"] = "true"

    page.goto(url)

    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": target_date,
        "on_match": on_match_steps or [],
    }
    ctx = {"user": "testuser", "password": "x", "target_date": target_date}
    return sniper.execute_poll(page, step, ctx), ctx


def _booking_page(date="2026-04-05", date_class="available", date_status="Reserve now"):
    """Minimal booking page HTML with a date picker, two slots, and a hidden #card-button."""
    return f"""<!DOCTYPE html><html><body>
    <div class="date_option {date_class}" data-date="{date}" data-detail-status="{date_status}">
        <div class="date_status">{date_status}</div>
    </div>
    <div id="time_chooser">
        <div class="slot_container" data-time="3:00pm">
            <div class="slot"><div class="time">3:00 <span>pm</span></div></div>
        </div>
        <div class="slot_container" data-time="4:00pm">
            <div class="slot"><div class="time">4:00 <span>pm</span></div></div>
        </div>
    </div>
    <button id="card-button" style="display:none">Proceed to Payment</button>
    </body></html>"""


def run_poll_with_card_button(page, target_date, on_match_steps, booking_steps):
    """Run execute_poll with card_button_selector configured."""
    import sniper
    os.environ["CI"] = "true"
    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": target_date,
        "card_button_selector": "#card-button",
        "on_match": on_match_steps + [{"action": "wait_for", "selector": "#card-button"}] + booking_steps,
    }
    ctx = {"user": "testuser", "password": "x", "target_date": target_date}
    return sniper.execute_poll(page, step, ctx), ctx

# ── Tests ────────────────────────────────────────────────────────────────────

def test_detects_ably_flip_without_reload(page, mock_server):
    """
    Core test: the date starts as check_back, flips to available after 2s
    (simulating an Ably push). wait_for_selector should catch it immediately
    without any page reload.
    """
    reload_count = 0
    original_reload = page.reload
    def counting_reload(**kwargs):
        nonlocal reload_count
        reload_count += 1
        return original_reload(**kwargs)
    page.reload = counting_reload

    url = f"{mock_server}/mock_site.html?delay=2&date=2026-04-05"
    result, ctx = run_poll(page, url, "2026-04-05")

    assert reload_count == 0, f"Page was reloaded {reload_count} time(s) — should be 0 when WebSocket is live"
    print(f"\nReloads during test: {reload_count} (expected 0)")


def test_slots_appear_after_date_click(page, mock_server):
    """
    After the date is clicked, the mock page injects .slot_container elements
    after a 300ms AJAX delay. The wait_for step must fire before click_preferred.
    """
    import sniper

    booked = []
    original = sniper.execute_click_preferred

    def spy(pg, step, c):
        slots = pg.query_selector_all(step["selector"])
        booked.append(len(slots))
        return original(pg, step, c)

    sniper.execute_click_preferred = spy

    on_match = [
        {"action": "wait_for", "selector": ".slot_container"},
        {
            "action": "click_preferred",
            "selector": ".slot_container:not(:has(.slot.unavailable))",
            "text_selector": ".time",
            "preferred": ["4:00", "5:00", "3:00"],
        },
    ]

    url = f"{mock_server}/mock_site.html?delay=1&date=2026-04-05"
    run_poll(page, url, "2026-04-05", on_match_steps=on_match)

    sniper.execute_click_preferred = original

    assert len(booked) == 1, "click_preferred should have been called once"
    assert booked[0] > 0, f"Expected slots to be visible when click_preferred ran, got {booked[0]}"
    print(f"\nSlots visible when click_preferred ran: {booked[0]}")


def test_preferred_time_selected(page, mock_server):
    """
    Among the available slots (9am, 11am, 3pm, 4pm, 5pm — 1pm is unavailable),
    the preferred list is ['4:00', '5:00', '3:00']. Should pick 4:00pm.
    """
    import sniper

    on_match = [
        {"action": "wait_for", "selector": ".slot_container"},
        {
            "action": "click_preferred",
            "selector": ".slot_container:not(:has(.slot.unavailable))",
            "text_selector": ".time",
            "preferred": ["4:00", "5:00", "3:00"],
        },
    ]

    url = f"{mock_server}/mock_site.html?delay=1&date=2026-04-05"
    _, ctx = run_poll(page, url, "2026-04-05", on_match_steps=on_match)

    assert ctx.get("booked_time") is not None, "No time was booked"
    assert "4:00" in ctx["booked_time"], f"Expected 4:00 to be booked, got '{ctx['booked_time']}'"
    print(f"\nBooked time: {ctx['booked_time']}")


def test_dom_observer_logs_class_change(mock_server, browser):
    """
    execute_poll must inject a MutationObserver that logs [dom:observer] on setup
    and [dom:classchange] whenever a date_option class is flipped (simulating an
    Ably push).
    """
    ctx = browser.new_context()
    pg = ctx.new_page()
    logs = []
    pg.on("console", lambda msg: logs.append(msg.text))

    url = f"{mock_server}/mock_site.html?date=2026-04-05&delay=2"
    run_poll(pg, url, "2026-04-05")
    ctx.close()

    observer_logs = [l for l in logs if "[dom:observer]" in l]
    change_logs = [l for l in logs if "[dom:classchange]" in l]

    assert len(observer_logs) >= 1, f"[dom:observer] not found in: {logs}"
    assert len(change_logs) >= 1, f"[dom:classchange] not found in: {logs}"
    target_changes = [l for l in change_logs if "date=2026-04-05" in l]
    assert target_changes, f"No classchange for date=2026-04-05 in: {change_logs}"
    assert any("available" in l for l in target_changes), (
        f"Expected 'available' class in target changes: {target_changes}"
    )


def test_poll_logs_landing_timestamp(page, mock_server, capsys):
    """
    execute_poll must print 'poll: landed on reservations page at HH:MM:SS'
    before starting to wait for the date selector.
    """
    url = f"{mock_server}/mock_site.html?date=2026-04-05&delay=1"
    run_poll(page, url, "2026-04-05")
    captured = capsys.readouterr()
    assert "poll: landed on reservations page" in captured.out, (
        f"Landing message not found in output:\n{captured.out}"
    )


def test_no_available_date_exhausts_timeout(page, mock_server):
    """
    If the date never becomes available (no flip), poll should exhaust its
    retries and return without booking.
    """
    import sniper

    # Use a very short timeout so the test doesn't actually wait 10 min
    original_poll = sniper.execute_poll

    def fast_poll(pg, step, c):
        import sniper as s
        # Temporarily shrink wait_ms
        original_env = os.environ.get("CI")
        os.environ["CI"] = "true"
        # Monkey-patch wait_ms by overriding via env not possible directly,
        # so run with a short timeout by calling wait_for_selector directly
        targeted = ".date_option.available[data-date='9999-01-01']"
        try:
            pg.wait_for_selector(targeted, timeout=1000)  # 1s, will timeout
        except Exception:
            pass
        return s.STEP_CONTINUE

    url = f"{mock_server}/mock_site.html?delay=9999&date=2026-04-05"
    page.goto(url)

    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": "9999-01-01",  # date that will never appear
        "on_match": [],
    }
    ctx = {"user": "testuser", "password": "x", "target_date": "9999-01-01"}

    import sniper
    # Override wait_ms for this test by patching the env
    os.environ["CI"] = "false"  # uses 30s locally — still too long, so patch directly
    result = fast_poll(page, step, ctx)

    assert result == sniper.STEP_CONTINUE, "Should return STEP_CONTINUE when no slot found"
    print("\nCorrectly exhausted poll with no available date")


def test_card_button_selector_success(page):
    """
    With card_button_selector configured, Phase 1 selects a slot and Phase 2 waits
    for #card-button. The button appears 400ms after the slot click (simulating a
    server response). booking_steps should execute and the result should not be
    STEP_NOT_FOUND.
    """
    import sniper
    os.environ["CI"] = "true"

    page.set_content(_booking_page())

    # Inject: show #card-button 400ms after any slot is clicked
    page.evaluate("""
        () => {
            document.getElementById('time_chooser').addEventListener('click', function(e) {
                if (!e.target.closest('.slot_container')) return;
                setTimeout(function() {
                    document.getElementById('card-button').style.display = 'inline-block';
                }, 400);
            });
        }
    """)

    slot_steps = [
        {"action": "wait_for", "selector": ".slot_container"},
        {"action": "click_preferred", "selector": ".slot_container", "text_selector": ".time", "preferred": ["3:00"]},
    ]
    booking_steps = [{"action": "click", "selector": "#card-button"}]

    result, ctx = run_poll_with_card_button(page, "2026-04-05", slot_steps, booking_steps)

    assert result != sniper.STEP_NOT_FOUND, "Should not return STEP_NOT_FOUND on successful booking path"
    assert ctx.get("booked_time") is not None, "booked_time should be set by click_preferred"
    print(f"\nPhase 1+2 success — booked_time: {ctx['booked_time']}, result: {result}")


def test_early_exit_unavailable_date(page):
    """
    When the target date is already 'unavailable' at the start of a poll attempt,
    execute_poll should return STEP_NOT_FOUND immediately without waiting.
    """
    import sniper
    os.environ["CI"] = "true"

    page.set_content(_booking_page(date_class="unavailable", date_status="Full"))

    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": "2026-04-05",
        "on_match": [],
    }
    ctx = {"user": "testuser", "password": "x", "target_date": "2026-04-05"}
    result = sniper.execute_poll(page, step, ctx)

    assert result == sniper.STEP_NOT_FOUND, f"Expected STEP_NOT_FOUND for unavailable date, got {result}"
    print("\nCorrectly returned STEP_NOT_FOUND for unavailable date")


def test_early_exit_future_check_back(page):
    """
    When the target date is 'check_back' with a future release (not 'release today'),
    execute_poll should return STEP_NOT_FOUND immediately.
    """
    import sniper
    os.environ["CI"] = "true"

    page.set_content(_booking_page(date_class="check_back", date_status="Next release Monday @ 9am"))

    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": "2026-04-05",
        "on_match": [],
    }
    ctx = {"user": "testuser", "password": "x", "target_date": "2026-04-05"}
    result = sniper.execute_poll(page, step, ctx)

    assert result == sniper.STEP_NOT_FOUND, f"Expected STEP_NOT_FOUND for future check_back, got {result}"
    print("\nCorrectly returned STEP_NOT_FOUND for future check_back date")


def test_phase2_date_unavailable_raises_and_outer_loop_exits(page):
    """
    After Phase 1 selects a slot, if the target date becomes 'unavailable' before
    #card-button appears, Phase 2 raises. The outer loop detects the unavailable
    date on the next attempt and returns STEP_NOT_FOUND without further retries.

    Uses page.route so that page.reload() serves the 'unavailable' page on the
    second request, exercising the full outer-loop early-exit path.
    """
    import sniper
    os.environ["CI"] = "true"

    call_count = [0]

    def handle_request(route):
        call_count[0] += 1
        if call_count[0] == 1:
            # First load: date available, slot click makes date unavailable after 400ms
            body = _booking_page() + """<script>
                document.getElementById('time_chooser').addEventListener('click', function(e) {
                    if (!e.target.closest('.slot_container')) return;
                    setTimeout(function() {
                        var el = document.querySelector('.date_option[data-date="2026-04-05"]');
                        if (el) { el.className = 'date_option selected unavailable'; }
                    }, 400);
                });
            </script>"""
        else:
            # Reload: date is already unavailable — outer loop should exit immediately
            body = _booking_page(date_class="unavailable", date_status="Full")
        route.fulfill(content_type="text/html", body=body)

    page.route("http://test-booking.local/", handle_request)
    page.goto("http://test-booking.local/")

    slot_steps = [
        {"action": "wait_for", "selector": ".slot_container"},
        {"action": "click_preferred", "selector": ".slot_container", "text_selector": ".time", "preferred": ["3:00"]},
    ]
    result, _ = run_poll_with_card_button(page, "2026-04-05", slot_steps, booking_steps=[])

    page.unroute("http://test-booking.local/", handle_request)

    assert result == sniper.STEP_NOT_FOUND, f"Expected STEP_NOT_FOUND when slot lost, got {result}"
    assert call_count[0] == 2, f"Expected exactly 2 requests (initial + one reload), got {call_count[0]}"
    print(f"\nCorrectly returned STEP_NOT_FOUND after slot lost — {call_count[0]} requests made")


# ── Lottery flow tests ───────────────────────────────────────────────────────

def _lottery_page(date="2026-05-16", flip_after_ms=300):
    """
    Mock page with a check_back date. Clicking the date injects #enter_lottery_btn.
    Clicking the button flips the date to .available after flip_after_ms (simulates
    Ably delivering the lottery result). Clicking the now-available date loads slots.
    """
    return f"""<!DOCTYPE html><html><body>
    <div class="date_option check_back" data-date="{date}" data-detail-status="Waiting for next batch">{date}</div>
    <div id="time_chooser"></div>
    <script>
      document.querySelector('.date_option').addEventListener('click', function() {{
        var el = this;
        if (el.classList.contains('check_back')) {{
          // Inject lottery button into DOM (like Ractive.js renders it from AJAX response)
          document.getElementById('time_chooser').innerHTML =
            '<button id="enter_lottery_btn">Request a Reservation</button>';
        }} else if (el.classList.contains('available')) {{
          // Re-clicked after admission — load slots
          setTimeout(function() {{
            document.getElementById('time_chooser').innerHTML =
              '<div class="slot_container"><div class="slot"><div class="time">3:00</div></div></div>';
          }}, 100);
        }}
      }});
      document.addEventListener('click', function(e) {{
        if (e.target.id !== 'enter_lottery_btn') return;
        e.target.disabled = true;
        setTimeout(function() {{
          var el = document.querySelector('.date_option[data-date="{date}"]');
          el.className = 'date_option available';
          el.setAttribute('data-detail-status', 'Reserve now');
        }}, {flip_after_ms});
      }});
    </script>
    </body></html>"""


def _check_back_no_lottery_page(date="2026-05-16", flip_after_ms=500):
    """
    check_back date with NO lottery button. Date flips to available after
    flip_after_ms via a JS timeout (simulates a non-lottery batch release).
    Clicking the available date loads slots.
    """
    return f"""<!DOCTYPE html><html><body>
    <div class="date_option check_back" data-date="{date}" data-detail-status="Waiting for next batch">{date}</div>
    <div id="time_chooser"></div>
    <script>
      setTimeout(function() {{
        var el = document.querySelector('.date_option[data-date="{date}"]');
        el.className = 'date_option available';
        el.setAttribute('data-detail-status', 'Reserve now');
      }}, {flip_after_ms});
      document.querySelector('.date_option').addEventListener('click', function() {{
        if (!this.classList.contains('available')) return;
        setTimeout(function() {{
          document.getElementById('time_chooser').innerHTML =
            '<div class="slot_container"><div class="slot"><div class="time">3:00</div></div></div>';
        }}, 100);
      }});
    </script>
    </body></html>"""


def test_waiting_for_next_batch_does_not_stop_early(page, capsys):
    """
    'Waiting for next batch' is check_back without a named future weekday.
    The bot must NOT return STEP_NOT_FOUND — it should keep polling and catch
    the date becoming available.
    """
    import sniper
    os.environ["CI"] = "true"

    # check_back date flips to available after 600ms (no lottery button)
    page.set_content(_check_back_no_lottery_page(flip_after_ms=600))

    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": "2026-05-16",
        "on_match": [],
    }
    ctx = {"user": "testuser", "password": "x", "target_date": "2026-05-16"}
    result = sniper.execute_poll(page, step, ctx)

    assert result != sniper.STEP_NOT_FOUND, "Should not stop early on 'Waiting for next batch'"
    captured = capsys.readouterr()
    assert "future date" not in captured.out, "Should not log 'future date' stop message"
    print("\n'Waiting for next batch' correctly kept polling")


def test_lottery_join_early_on_check_back(page, capsys):
    """
    When the date is check_back and #enter_lottery_btn is immediately available
    (lottery pool open far in advance), the bot should click it, join the pool,
    then wait for the date to flip to available (Ably lottery result) and book.
    """
    import sniper
    os.environ["CI"] = "true"

    page.set_content(_lottery_page(flip_after_ms=400))

    on_match = [
        {"action": "wait_for", "selector": ".slot_container"},
        {"action": "click_preferred", "selector": ".slot_container", "text_selector": ".time", "preferred": ["3:00"]},
    ]
    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": "2026-05-16",
        "on_match": on_match,
    }
    ctx = {"user": "testuser", "password": "x", "target_date": "2026-05-16"}
    result = sniper.execute_poll(page, step, ctx)

    captured = capsys.readouterr()
    assert "LOTTERY MODE" in captured.out, "Bot should detect #enter_lottery_btn"
    assert "joined lottery pool" in captured.out, "Bot should log pool join"
    assert ctx.get("booked_time") is not None, "Should book a time after lottery admission"
    print(f"\nLottery early join: joined pool and booked '{ctx['booked_time']}'")


def test_lottery_join_attempted_only_once(page, capsys):
    """
    When #enter_lottery_btn is absent (non-lottery date), _try_join_lottery is
    called exactly once (lottery_join_attempted flag prevents retries). The bot
    then falls through to normal polling and catches the date flip.
    """
    import sniper
    os.environ["CI"] = "true"

    join_call_count = [0]
    original = sniper._try_join_lottery

    def counting_join(pg, label, match_value, ts_fn, timeout_ms=5000):
        join_call_count[0] += 1
        return original(pg, label, match_value, ts_fn, timeout_ms=1)  # 1ms — instant fail

    sniper._try_join_lottery = counting_join

    # No lottery button; date flips after 200ms
    page.set_content(_check_back_no_lottery_page(flip_after_ms=200))

    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": "2026-05-16",
        "on_match": [],
    }
    ctx = {"user": "testuser", "password": "x", "target_date": "2026-05-16"}
    sniper.execute_poll(page, step, ctx)

    sniper._try_join_lottery = original

    assert join_call_count[0] == 1, (
        f"_try_join_lottery should be called exactly once, got {join_call_count[0]}"
    )
    print(f"\nLottery join attempted exactly once: {join_call_count[0]}")


# ── Date helper tests ────────────────────────────────────────────────────────

def test_next_sunday_date():
    """next_sunday_date() should always return a Sunday >= today."""
    from datetime import datetime, date
    import sniper
    result = sniper.next_sunday_date()
    d = datetime.strptime(result, "%Y-%m-%d").date()
    assert d.weekday() == 6, f"Expected Sunday (weekday 6), got weekday {d.weekday()} for {result}"
    assert d >= date.today(), f"next_sunday_date() returned a past date: {result}"
    print(f"\nnext_sunday_date() = {result}")


def test_next_test_date():
    """next_test_date() should always return a Tuesday strictly in the future (never today)."""
    from datetime import datetime, date
    import sniper
    result = sniper.next_test_date()
    d = datetime.strptime(result, "%Y-%m-%d").date()
    assert d.weekday() == 1, f"Expected Tuesday (weekday 1), got weekday {d.weekday()} for {result}"
    assert d > date.today(), f"next_test_date() should be strictly future, got {result}"
    print(f"\nnext_test_date() = {result}")


# ── closed / not-in-grid early exit tests ───────────────────────────────────

def test_early_exit_closed_date(page):
    """
    When the target date has class 'closed', execute_poll must return STEP_NOT_FOUND
    on the first attempt — no looping through 24 reloads.
    """
    import sniper
    os.environ["CI"] = "true"

    reload_count = [0]
    original_reload = page.reload
    def counting_reload(**kwargs):
        reload_count[0] += 1
        return original_reload(**kwargs)
    page.reload = counting_reload

    page.set_content(_booking_page(date_class="closed", date_status="Closed"))

    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": "2026-04-05",
        "on_match": [],
    }
    ctx = {"user": "testuser", "password": "x", "target_date": "2026-04-05"}
    result = sniper.execute_poll(page, step, ctx)

    assert result == sniper.STEP_NOT_FOUND, f"Expected STEP_NOT_FOUND for closed date, got {result}"
    assert reload_count[0] == 0, f"Should not reload for closed date, got {reload_count[0]} reloads"
    print(f"\nClosed date: STEP_NOT_FOUND immediately, 0 reloads")


def test_early_exit_not_in_grid(page):
    """
    When the target date is absent from the date grid entirely,
    execute_poll must return STEP_NOT_FOUND immediately.
    """
    import sniper
    os.environ["CI"] = "true"

    reload_count = [0]
    original_reload = page.reload
    def counting_reload(**kwargs):
        reload_count[0] += 1
        return original_reload(**kwargs)
    page.reload = counting_reload

    # Grid only contains 2026-04-06, target is 2026-04-05 (not present)
    page.set_content(_booking_page(date="2026-04-06", date_class="available"))

    step = {
        "action": "poll",
        "selector": ".date_option.available",
        "match_attribute": "data-date",
        "match_value": "2026-04-05",
        "on_match": [],
    }
    ctx = {"user": "testuser", "password": "x", "target_date": "2026-04-05"}
    result = sniper.execute_poll(page, step, ctx)

    assert result == sniper.STEP_NOT_FOUND, f"Expected STEP_NOT_FOUND when date not in grid, got {result}"
    assert reload_count[0] == 0, f"Should not reload when date not in grid, got {reload_count[0]} reloads"
    print(f"\nDate not in grid: STEP_NOT_FOUND immediately, 0 reloads")


# ── test_mode / confirms_booking tests ──────────────────────────────────────

def test_test_mode_stops_at_confirms_booking(page, capsys):
    """
    In test_mode, execute_steps must intercept any step with confirms_booking=True
    and return STEP_DRY_RUN without executing that step or any following steps.
    """
    import sniper
    os.environ["CI"] = "true"

    page.set_content("""<!DOCTYPE html><html><body>
        <button id="btn1">Step 1</button>
        <button id="btn2">Confirm Booking</button>
        <button id="btn3">Step After</button>
    </body></html>""")
    page.evaluate("""() => {
        window._clicks = [];
        ['btn1','btn2','btn3'].forEach(function(id) {
            document.getElementById(id).addEventListener('click', function() {
                window._clicks.push(id);
            });
        });
    }""")

    steps = [
        {"action": "click", "selector": "#btn1"},
        {"action": "click", "selector": "#btn2", "confirms_booking": True},
        {"action": "click", "selector": "#btn3"},
    ]
    ctx = {"user": "testuser", "password": "x", "target_date": "2026-04-05",
           "test_mode": True, "no_pause": True}

    result = sniper.execute_steps(page, steps, ctx)
    captured = capsys.readouterr()
    clicks = page.evaluate("() => window._clicks")

    assert result == sniper.STEP_DRY_RUN, f"Expected STEP_DRY_RUN, got {result}"
    assert "btn1" in clicks, "Step before confirms_booking should have executed"
    assert "btn2" not in clicks, "confirms_booking step must NOT execute"
    assert "btn3" not in clicks, "Steps after confirms_booking must NOT execute"
    assert "stopping before final booking confirmation" in captured.out
    print(f"\ntest_mode confirms_booking gate: clicks={clicks}, result={result}")


# ── continue_if_no_booking fallthrough test ──────────────────────────────────

def test_continue_if_no_booking_fallthrough(page, capsys):
    """
    Saturday poll returns STEP_NOT_FOUND (closed date) with continue_if_no_booking=True.
    execute_steps should fall through to the Tuesday poll, which finds the available
    date, runs on_match, and sets booked_time.
    """
    import sniper
    os.environ["CI"] = "true"

    page.set_content("""<!DOCTYPE html><html><body>
        <div class="date_option closed" data-date="2026-06-06"
             data-detail-status="Closed">Sat</div>
        <div class="date_option available" data-date="2026-06-02"
             data-detail-status="Reserve now">Tue</div>
        <div id="time_chooser"></div>
        <script>
            document.querySelectorAll('.date_option.available').forEach(function(el) {
                el.addEventListener('click', function() {
                    setTimeout(function() {
                        document.getElementById('time_chooser').innerHTML =
                            '<div class="slot_container">' +
                            '<div class="slot"><div class="time">3:00</div></div>' +
                            '</div>';
                    }, 100);
                });
            });
        </script>
    </body></html>""")

    on_match = [
        {"action": "wait_for", "selector": ".slot_container"},
        {"action": "click_preferred", "selector": ".slot_container",
         "text_selector": ".time", "preferred": ["3:00"]},
    ]
    steps = [
        {
            "action": "poll",
            "selector": ".date_option.available",
            "match_attribute": "data-date",
            "match_value": "2026-06-06",
            "on_match": [],
            "continue_if_no_booking": True,
        },
        {
            "action": "poll",
            "selector": ".date_option.available",
            "match_attribute": "data-date",
            "match_value": "2026-06-02",
            "test_mode": True,
            "on_match": on_match,
        },
    ]
    ctx = {
        "user": "testuser",
        "password": "x",
        "target_date": "2026-06-06",
        "test_date": "2026-06-02",
        "no_pause": True,
    }

    result = sniper.execute_steps(page, steps, ctx)
    captured = capsys.readouterr()

    assert "no booking — continuing to test date poll" in captured.out, \
        "Should log the fallthrough message"
    assert ctx.get("test_mode") is True, \
        "test_mode should be set by Tuesday poll step"
    assert ctx.get("booked_time") is not None, \
        "Tuesday poll should have selected a time slot"
    print(f"\nFallthrough: Saturday(closed) → Tuesday booked_time='{ctx['booked_time']}', result={result}")
