"""One-off: screenshot the three reference pages with Playwright."""
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "_screenshots"
OUT.mkdir(exist_ok=True)

PAGES = [
    ("01-index.png",       "http://localhost:8765/index.html"),
    ("02-category.png",    "http://localhost:8765/category.html?cat=sectors"),
    ("03-report.png",      "http://localhost:8765/reports/2026-05-22/sectors-daily.html"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                              device_scale_factor=2)
    page = ctx.new_page()
    for fname, url in PAGES:
        page.goto(url)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3500)  # 2.5s scroll-fade safety net + reveal + fonts
        out = OUT / fname
        page.screenshot(path=str(out), full_page=True)
        print(f"[OK] {out}")
    browser.close()
