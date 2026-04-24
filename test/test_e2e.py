"""
Full end-to-end test of the booking flow against a mock site served via page.route().

Covers:
  login → confirm_information redirect → reservations/new →
  date selection → slot selection (Phase 1) →
  server-driven card-button wait (Phase 2) →
  payment options → confirmation

Run with:
    pytest test/test_e2e.py -v -s
"""
import os
import sys
import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

# ── Mock page HTML ────────────────────────────────────────────────────────────

_LOGIN_HTML = """<!DOCTYPE html><html><body>
<form id="lf">
  <input name="visitor[email]" type="email" />
  <input name="visitor[password]" type="password" />
  <input type="submit" value="Log in" />
</form>
<script>
  document.getElementById('lf').addEventListener('submit', function(e) {
    e.preventDefault();
    window.location.href = 'https://mock.local/visitors/confirm_information';
  });
</script>
</body></html>"""

_CONFIRM_HTML = """<!DOCTYPE html><html><body>
<form id="cf">
  <input type="submit" value="Continue" />
</form>
<script>
  document.getElementById('cf').addEventListener('submit', function(e) {
    e.preventDefault();
    window.location.href = 'https://mock.local/reservations/new';
  });
</script>
</body></html>"""

_RESERVATIONS_HTML = """<!DOCTYPE html><html><body>
<div id="date_picker">
  <div class="date_option available"
       data-date="2026-04-25"
       data-detail-status="Reserve now">Apr 25</div>
</div>
<div id="time_chooser"></div>
<button id="card-button" style="display:none">Proceed to Payment</button>
<div id="price_options" style="display:none">
  <div class="price_option no_contribution">No contribution</div>
</div>
<script>
  var cardClicks = 0;

  // Date click → load slots after 200ms (simulates AJAX)
  document.getElementById('date_picker').addEventListener('click', function(e) {
    var d = e.target.closest('.date_option');
    if (!d) return;
    d.classList.add('selected');
    setTimeout(function() {
      document.getElementById('time_chooser').innerHTML =
        '<div class="slot_container">' +
          '<div class="slot"><div class="time">3:00 <span>pm</span></div></div>' +
        '</div>';

      // Slot click → show card-button after 200ms (Phase 2 waits for this)
      document.getElementById('time_chooser').addEventListener('click', function(e) {
        if (!e.target.closest('.slot_container')) return;
        setTimeout(function() {
          document.getElementById('card-button').style.display = 'inline-block';
        }, 200);
      });
    }, 200);
  });

  // card-button click 1: hide it, show price options
  // card-button click 2: navigate to confirmation
  document.getElementById('card-button').addEventListener('click', function() {
    cardClicks++;
    if (cardClicks === 1) {
      this.style.display = 'none';
      document.getElementById('price_options').style.display = 'block';
    } else {
      window.location.href = 'https://mock.local/reservations/confirmation';
    }
  });

  // Price option click → re-show card-button for the second click
  document.getElementById('price_options').addEventListener('click', function(e) {
    if (e.target.closest('.price_option.no_contribution')) {
      document.getElementById('card-button').style.display = 'inline-block';
    }
  });
</script></body></html>"""

_CONFIRMATION_HTML = """<!DOCTYPE html><html><body>
<h1>Booking Confirmed!</h1>
</body></html>"""

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()

# ── Test ──────────────────────────────────────────────────────────────────────

def test_full_booking_flow(browser):
    """
    Runs the complete sniper step sequence against a mock site.

    The mock simulates the real site's behaviour:
      - Login form submits and redirects to confirm_information
      - Navigating to reservations/new first redirects to confirm_information,
        then to reservations/new after clicking Continue
      - Slot click triggers a delayed card-button (Phase 2 server-resolution path)
      - Two card-button clicks: first opens price options, second confirms booking
    """
    import sniper
    os.environ["CI"] = "true"

    bctx = browser.new_context()
    page = bctx.new_page()
    page.on("console", lambda msg: print(f"  [e2e:{msg.type}] {msg.text}"))

    # Track how many times /reservations/new is requested so we can simulate
    # the first visit redirecting to confirm_information (as the real site does).
    reservations_visits = [0]

    def handle_route(route, request):
        url   = request.url
        method = request.method

        if "/visitors/login" in url:
            route.fulfill(content_type="text/html", body=_LOGIN_HTML)

        elif "/visitors/confirm_information" in url:
            route.fulfill(content_type="text/html", body=_CONFIRM_HTML)

        elif "/reservations/new" in url:
            reservations_visits[0] += 1
            if reservations_visits[0] == 1:
                # First visit: JS redirect to confirm_information (real-site behaviour)
                route.fulfill(content_type="text/html",
                              body='<script>window.location.href="https://mock.local/visitors/confirm_information";</script>')
            else:
                route.fulfill(content_type="text/html", body=_RESERVATIONS_HTML)

        elif "/reservations/confirmation" in url:
            route.fulfill(content_type="text/html", body=_CONFIRMATION_HTML)

        else:
            route.fulfill(status=404, body="not found")

    bctx.route("https://mock.local/**", handle_route)

    # Mirror sites.yaml steps but pointing at mock.local
    steps = [
        {"action": "navigate",   "url": "https://mock.local/visitors/login"},
        {"action": "wait_for",   "selector": 'input[name="visitor[email]"]'},
        {"action": "fill",       "selector": 'input[name="visitor[email]"]',   "value": "test@example.com"},
        {"action": "fill",       "selector": 'input[name="visitor[password]"]', "value": "testpass"},
        {"action": "click",      "selector": 'input[type="submit"][value="Log in"]'},
        {"action": "wait_for_load"},
        {"action": "assert_url_not", "url": "https://mock.local/visitors/login", "error": "Login failed"},
        {"action": "navigate",   "url": "https://mock.local/reservations/new"},
        {"action": "if_on_url",
         "url": "https://mock.local/visitors/confirm_information",
         "then": [
             {"action": "click",        "selector": 'input[type="submit"][value="Continue"]'},
             {"action": "wait_for_url", "url": "**/reservations/new"},
         ]},
        {"action": "poll",
         "selector": ".date_option.available",
         "match_attribute": "data-date",
         "match_value": "2026-04-25",
         "card_button_selector": "#card-button",
         "slot_retries": 2,
         "on_match": [
             {"action": "wait_for", "selector": ".slot_container"},
             {"action": "click_preferred",
              "selector": ".slot_container",
              "text_selector": ".time",
              "preferred": ["3:00"]},
             # wait_for #card-button is the Phase 2 split point
             {"action": "wait_for", "selector": "#card-button"},
             # booking_steps below
             {"action": "click",    "selector": "#card-button"},
             {"action": "wait_for", "selector": ".price_option.no_contribution"},
             {"action": "click",    "selector": ".price_option.no_contribution"},
             {"action": "wait_for", "selector": "#card-button"},
             {"action": "click",    "selector": "#card-button"},
             {"action": "wait_for_url", "url": "**/reservations/confirmation**", "timeout": 15000},
             {"action": "assert_url",
              "url": "https://mock.local/reservations/confirmation",
              "success": "BOOKING CONFIRMED!"},
         ]},
    ]

    test_ctx = {
        "user": "testuser",
        "password": "testpass",
        "target_date": "2026-04-25",
        "no_pause": True,
    }

    result = sniper.execute_steps(page, steps, test_ctx)
    bctx.close()

    assert result == sniper.STEP_SUCCESS, f"Expected STEP_SUCCESS, got {result}"
    assert test_ctx.get("booked_time") is not None, "booked_time should be set by click_preferred"
    assert reservations_visits[0] == 2, \
        f"Expected 2 visits to /reservations/new (initial redirect + post-confirm), got {reservations_visits[0]}"
    print(f"\nFull E2E booking: PASSED")
    print(f"  booked_time : {test_ctx['booked_time']}")
    print(f"  result      : {result}")
    print(f"  /reservations/new visits: {reservations_visits[0]}")
