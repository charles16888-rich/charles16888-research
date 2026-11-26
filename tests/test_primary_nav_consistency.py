from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# AI 研析 added 2026-07-14; 美股觀察 (us-watch) and 新聞解析 (news-analysis) added 2026-11-26.
EXPECTED_LABELS = ["族群", "大盤", "行事曆", "選擇權", "籌碼", "個股", "研報統計", "美股觀察", "AI 研析", "新聞解析"]
# Already-published report pages keep the nav they were built with (not backfilled).
HISTORICAL_REPORT_LABELS = [
    ["族群", "大盤", "行事曆", "選擇權", "籌碼", "個股", "研報統計"],
    ["族群", "大盤", "行事曆", "選擇權", "籌碼", "個股", "研報統計", "AI 研析"],
    EXPECTED_LABELS,
]
NAV_RE = re.compile(r'<nav class="nav" aria-label="Primary">(.*?)</nav>', re.S)
LINK_RE = re.compile(r'<a\b[^>]*class="[^"]*\bnav__link\b[^"]*"[^>]*>(.*?)</a>', re.S)


class PrimaryNavConsistencyTest(unittest.TestCase):
    def nav_labels(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        match = NAV_RE.search(text)
        self.assertIsNotNone(match, f"missing primary nav: {path}")
        return [re.sub(r"<[^>]+>", "", label).strip() for label in LINK_RE.findall(match.group(1))]

    def test_shell_pages_have_expected_nav(self) -> None:
        for page in (ROOT / "index.html", ROOT / "category.html"):
            with self.subTest(page=page.name):
                self.assertEqual(EXPECTED_LABELS, self.nav_labels(page))

    def test_report_template_uses_same_primary_nav_labels(self) -> None:
        self.assertEqual(EXPECTED_LABELS, self.nav_labels(ROOT / "templates" / "report-wrap.html"))

    def test_generators_use_same_primary_nav_labels(self) -> None:
        checked = 0
        for path in sorted((ROOT / "tools").glob("*.py")):
            if not NAV_RE.search(path.read_text(encoding="utf-8")):
                continue
            checked += 1
            with self.subTest(file=path.name):
                self.assertEqual(EXPECTED_LABELS, self.nav_labels(path))
        self.assertGreater(checked, 0)

    def test_published_report_pages_use_a_known_nav(self) -> None:
        pages = [
            p
            for p in (ROOT / "reports").rglob("*.html")
            if "options/weekly_reports" not in p.as_posix()
            and NAV_RE.search(p.read_text(encoding="utf-8"))
        ]
        self.assertTrue(pages)
        for page in pages:
            with self.subTest(page=page.relative_to(ROOT).as_posix()):
                self.assertIn(self.nav_labels(page), HISTORICAL_REPORT_LABELS)


if __name__ == "__main__":
    unittest.main()
