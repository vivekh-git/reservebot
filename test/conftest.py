"""
Shared Playwright browser fixture — session-scoped so sync_playwright() is
only entered once per test session. Entering it twice causes an asyncio
event-loop conflict when pytest-asyncio is active.
"""
import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()
