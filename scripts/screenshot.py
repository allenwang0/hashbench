"""Save docs/report-light.png and docs/report-dark.png from site/index.html
with headless Chrome via Playwright. Needs Google Chrome installed, or run
`playwright install chromium` and pass --bundled."""
import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    bundled = "--bundled" in sys.argv
    url = (ROOT / "site" / "index.html").as_uri()
    (ROOT / "docs").mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if bundled else "chrome")
        for scheme in ("light", "dark"):
            page = browser.new_page(viewport={"width": 820, "height": 1400}, color_scheme=scheme,
                                    device_scale_factor=2)
            page.goto(url)
            page.screenshot(path=str(ROOT / "docs" / f"report-{scheme}.png"))
            page.close()
        page = browser.new_page(viewport={"width": 375, "height": 800})
        page.goto(url)
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        browser.close()
    if overflow:
        sys.exit("page scrolls horizontally at 375 px")


if __name__ == "__main__":
    main()
