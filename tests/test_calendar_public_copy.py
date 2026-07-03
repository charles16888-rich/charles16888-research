from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CalendarPublicCopyTests(unittest.TestCase):
    def test_financial_calendar_card_hides_source_name(self) -> None:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        entry = next(item for item in manifest["entries"] if item["id"] == "financial-calendar")
        public_text = json.dumps(
            {
                "summary": entry.get("summary"),
                "tags": entry.get("tags"),
                "source_pipeline": entry.get("source_pipeline"),
            },
            ensure_ascii=False,
        )
        self.assertNotIn("Money-Link", public_text)


if __name__ == "__main__":
    unittest.main()
