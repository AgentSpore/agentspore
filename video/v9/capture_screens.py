"""Capture real platform screenshots from public agentspore.com."""
import os
import time
from playwright.sync_api import sync_playwright

OUT = "/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/scenes"
os.makedirs(OUT, exist_ok=True)

TARGETS = [
    ("https://agentspore.com/agents/redditscoutagent", "shot_agent.png", 2500),
    ("https://agentspore.com/dashboard", "shot_dashboard.png", 2000),
    ("https://agentspore.com/", "shot_home.png", 1500),
]


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            color_scheme="dark",
        )
        page = ctx.new_page()
        for url, name, wait in TARGETS:
            try:
                print("navigate", url)
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(wait)
                # Scroll a bit to trigger lazy content
                page.evaluate("window.scrollBy(0, 200)")
                page.wait_for_timeout(500)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(500)
                out = os.path.join(OUT, name)
                page.screenshot(path=out, full_page=False)
                print("saved", out)
            except Exception as e:
                print("FAIL", url, e)
        browser.close()


if __name__ == "__main__":
    main()
