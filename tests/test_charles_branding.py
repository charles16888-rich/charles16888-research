from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PUBLIC_PATHS = [
    ROOT / "index.html",
    ROOT / "category.html",
    ROOT / "manifest.json",
    ROOT / "categories.json",
    ROOT / "assets" / "main.js",
    ROOT / "assets" / "calendar_events.json",
]

FORBIDDEN_BRAND_TERMS = [
    "Lynus' Research",
    'data-tenant="lynus"',
    '"tenant_id": "lynus"',
]


def public_text_files() -> list[Path]:
    paths = [path for path in PUBLIC_PATHS if path.exists()]
    paths.extend((ROOT / "reports").rglob("*.html"))
    return sorted(set(paths))


class CharlesBrandingTests(unittest.TestCase):
    def test_root_shell_has_charles_identity(self) -> None:
        for relative_path in ["index.html", "category.html", "assets/main.js"]:
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("charles16888", text, relative_path)

    def test_public_site_has_no_lynus_branding_tokens(self) -> None:
        hits: list[str] = []
        for path in public_text_files():
            text = path.read_text(encoding="utf-8")
            for term in FORBIDDEN_BRAND_TERMS:
                if term in text:
                    hits.append(f"{path.relative_to(ROOT)}: {term}")
        self.assertEqual([], hits)

    def test_root_theme_is_charles_light_theme(self) -> None:
        style = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
        self.assertIn("--bg:            #fffdf8;", style)
        self.assertNotIn("--bg:            #14110e;", style)


if __name__ == "__main__":
    unittest.main()
