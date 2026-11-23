"""Save docs/img/lookup.png (the lookup-vs-load figure, for the README) from
site/index.html with headless Chrome via Playwright, and fail if the page
scrolls sideways at 375 px. Needs Google Chrome installed, or run
`playwright install chromium` and pass --bundled."""
import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    bundled = "--bundled" in sys.argv
    url = (ROOT / "site" / "index.html").as_uri()
    (ROOT / "docs" / "img").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if bundled else "chrome")
        page = browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light",
                                device_scale_factor=2)
        page.goto(url)
        page.locator("#fig-lookup").screenshot(path=str(ROOT / "docs" / "img" / "lookup.png"))
        page.close()
        page = browser.new_page(viewport={"width": 375, "height": 800})
        page.goto(url)
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        browser.close()
    if overflow:
        sys.exit("page scrolls horizontally at 375 px")


if __name__ == "__main__":
    main()
