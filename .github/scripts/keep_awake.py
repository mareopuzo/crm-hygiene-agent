"""Open the Streamlit app in a headless browser and wake it if it's asleep."""

import os
import sys

from playwright.sync_api import sync_playwright

APP_URL = os.environ.get("APP_URL", "https://crm-hygiene-agent.streamlit.app/")
WAKE_BUTTON = "button:has-text('get this app back up')"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(10_000)

        wake = page.locator(WAKE_BUTTON)
        if wake.count() > 0:
            print("App was asleep, clicking wake button")
            wake.first.click()
            # Booting takes a while; stay on the page so the wake request completes.
            page.wait_for_timeout(90_000)
        else:
            print("App is awake")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
