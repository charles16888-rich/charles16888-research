from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_LABELS = ["族群", "大盤", "行事曆", "選擇權", "籌碼", "個股", "研報統計"]
NAV_RE = re.compile(r'<nav class="nav" aria-label="Primary">(.*?)</nav>', re.S)
LINK_RE = re.compile(r'<a\b[^>]*class="[^"]*\bnav__link\b[^"]*"[^>]*>(.*?)</a>', re.S)


class PrimaryNavConsistencyTest(unittest.TestCase):
    def nav_labels(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        match = NAV_RE.search(text)
        self.assertIsNotNone(match, f"missing primary nav: {path}")
        return [re.sub(r"<[^>]+>", "", label).strip() for label in LINK_RE.findall(match.group(1))]

    def test_primary_nav_labels_are_consistent_on_published_pages(self) -> None:
        pages = [
            ROOT / "index.html",
            ROOT / "category.html",
            ROOT / "reports" / "financial-calendar.html",
            ROOT / "reports" / "chip-concentration.html",
            ROOT / "reports" / "high-price-watch.html",
        ]
        for page in pages:
            with self.subTest(page=page.name):
                self.assertEqual(EXPECTED_LABELS, self.nav_labels(page))

    def test_all_report_primary_nav_labels_are_consistent(self) -> None:
        pages = [
            p
            for p in (ROOT / "reports").rglob("*.html")
            if "options/weekly_reports" not in p.as_posix()
            and NAV_RE.search(p.read_text(encoding="utf-8"))
        ]
        self.assertTrue(pages)
        for page in pages:
            with self.subTest(page=page.relative_to(ROOT).as_posix()):
                self.assertEqual(EXPECTED_LABELS, self.nav_labels(page))

    def test_report_template_uses_same_primary_nav_labels(self) -> None:
        self.assertEqual(EXPECTED_LABELS, self.nav_labels(ROOT / "templates" / "report-wrap.html"))


if __name__ == "__main__":
    unittest.main()
