from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_LABELS = ["族群", "大盤", "行事曆", "選擇權", "籌碼", "個股", "研報統計"]
CURRENT_LABELS = [*LEGACY_LABELS, "AI 研析"]
APPROVED_LABEL_SEQUENCES = {tuple(LEGACY_LABELS), tuple(CURRENT_LABELS)}
NAV_RE = re.compile(r'<nav class="nav" aria-label="Primary">(.*?)</nav>', re.S)
LINK_RE = re.compile(r'<a\b[^>]*class="[^"]*\bnav__link\b[^"]*"[^>]*>(.*?)</a>', re.S)


class PrimaryNavConsistencyTest(unittest.TestCase):
    def nav_labels(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        match = NAV_RE.search(text)
        self.assertIsNotNone(match, f"missing primary nav: {path}")
        return [re.sub(r"<[^>]+>", "", label).strip() for label in LINK_RE.findall(match.group(1))]

    def test_current_shell_uses_current_primary_nav(self) -> None:
        pages = [ROOT / "index.html", ROOT / "category.html"]
        for page in pages:
            with self.subTest(page=page.name):
                self.assertEqual(CURRENT_LABELS, self.nav_labels(page))

    def test_all_report_primary_nav_labels_use_an_approved_version(self) -> None:
        pages = [
            p
            for p in (ROOT / "reports").rglob("*.html")
            if "options/weekly_reports" not in p.as_posix()
            and NAV_RE.search(p.read_text(encoding="utf-8"))
        ]
        self.assertTrue(pages)
        for page in pages:
            with self.subTest(page=page.relative_to(ROOT).as_posix()):
                self.assertIn(tuple(self.nav_labels(page)), APPROVED_LABEL_SEQUENCES)

    def test_report_template_uses_current_primary_nav(self) -> None:
        self.assertEqual(CURRENT_LABELS, self.nav_labels(ROOT / "templates" / "report-wrap.html"))


if __name__ == "__main__":
    unittest.main()
