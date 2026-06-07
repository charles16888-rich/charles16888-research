"""
publish.py
==========
Single CLI entrypoint that pipelines must use to push content to the website.

Pipelines do NOT directly touch reports/, manifest.json, categories.json, or
git. They produce a markdown file + a meta JSON file, then call:

    python tools/publish.py add --content report.md --meta meta.json [--no-git]

publish.py is responsible for:
    1. Calling wrap_report to convert MD → wrapped HTML at the right path.
    2. Calling update_manifest to add/replace the corresponding entry.
    3. (Optional) git add + commit + push so Cloudflare Pages picks up the
       change. --no-git skips this; useful for local testing or batch backfill.

Meta JSON schema (the contract with pipelines):
    {
        "id":            "2026-05-22-sectors-daily",  // optional, auto-built
        "category":      "sectors",
        "category_name": "族群",
        "type":          "daily",
        "type_label":    "盤後快報",
        "date":          "2026-05-22",
        "time":          "20:00",
        "title":         "面板族群暴衝 9.87%，ABF 載板續強",
        "title_em":      "暴衝",
        "summary":       "盤後 74 個族群中 53 個收紅...",
        "lead":          "...same or different from summary...",
        "tags":          ["面板", "ABF載板"],
        "source_pipeline": "industry_map",
        "stats":         [{"label":"...","value":"...","color":"up"}],
        "volume":        1
    }

The output HTML path is derived deterministically:
    reports/{date}/{category}-{type}.html
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Make sibling tool importable
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from wrap_report import wrap_report  # noqa: E402

WEBSITE_ROOT = HERE.parent
REPORTS_DIR = WEBSITE_ROOT / "reports"


def derive_url(meta: dict) -> str:
    """The relative URL of the produced HTML, as recorded in manifest entries."""
    return f"reports/{meta['date']}/{meta['category']}-{meta['type']}.html"


def derive_output_path(meta: dict) -> Path:
    return WEBSITE_ROOT / derive_url(meta)


def derive_id(meta: dict) -> str:
    return f"{meta['date']}-{meta['category']}-{meta['type']}"


def derive_asset_prefix(output_path: Path) -> str:
    """Compute the right '../../' prefix based on output depth."""
    rel = output_path.relative_to(WEBSITE_ROOT)
    depth = len(rel.parts) - 1  # exclude the filename
    return "../" * depth


def to_wrap_meta(meta: dict, output_path: Path) -> dict:
    return {
        "category_id":   meta["category"],
        "category_name": meta.get("category_name", meta["category"]),
        "type_id":       meta["type"],
        "type_label":    meta.get("type_label", ""),
        "date":          meta["date"],
        "time":          meta.get("time", ""),
        "title":         meta["title"],
        "title_em":      meta.get("title_em", ""),
        "lead":          meta.get("lead") or meta.get("summary") or "",
        "volume":        meta.get("volume", 1),
        "asset_prefix":  derive_asset_prefix(output_path),
        "stats":         meta.get("stats", []),
    }


def to_manifest_entry(meta: dict) -> dict:
    return {
        "id":              derive_id(meta),
        "category":        meta["category"],
        "type":            meta["type"],
        "date":            meta["date"],
        "time":            meta.get("time", ""),
        "title":           meta["title"],
        "title_em":        meta.get("title_em", ""),
        "summary":         meta.get("summary", ""),
        "tags":            meta.get("tags", []),
        "source_pipeline": meta.get("source_pipeline", ""),
        "url":             derive_url(meta),
        "stats":           meta.get("stats", []),
    }


def run_update_manifest(entry: dict) -> None:
    """Call update_manifest.py via subprocess for clean separation."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, str(HERE / "update_manifest.py"), "add", "--meta", tmp_path],
            check=True,
            cwd=str(WEBSITE_ROOT),
        )
    finally:
        os.unlink(tmp_path)


def git_publish(messages: list[str]) -> None:
    """Stage all changes under website/, commit, and push."""
    if not (WEBSITE_ROOT / ".git").exists() and not (WEBSITE_ROOT.parent / ".git").exists():
        print("[SKIP] not in a git repo; skipping push")
        return

    subprocess.run(["git", "add", "."], check=True, cwd=str(WEBSITE_ROOT))
    msg = "publish: " + "; ".join(messages)
    # If nothing to commit, git returns 1; let that be non-fatal.
    res = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=str(WEBSITE_ROOT),
    )
    if res.returncode != 0:
        print("[INFO] nothing new to commit")
        return
    subprocess.run(["git", "push"], check=True, cwd=str(WEBSITE_ROOT))
    print(f"[OK] pushed: {msg}")


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_add(args) -> int:
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    required = ["category", "type", "date", "title"]
    missing = [k for k in required if not meta.get(k)]
    if missing:
        print(f"[ERR] missing required meta fields: {missing}", file=sys.stderr)
        return 1

    output_path = derive_output_path(meta)
    wrap_meta = to_wrap_meta(meta, output_path)
    wrap_report(Path(args.content), output_path, wrap_meta)
    print(f"[OK] wrote {output_path.relative_to(WEBSITE_ROOT)}")

    entry = to_manifest_entry(meta)
    run_update_manifest(entry)

    if not args.no_git:
        git_publish([f"{meta['category']}/{meta['type']} {meta['date']}"])
    else:
        print("[SKIP] --no-git: leaving git alone")

    return 0


# ── Entrypoint ───────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Publish a report to the website.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sa = sub.add_parser("add", help="Add a single report (md + meta).")
    sa.add_argument("--content", required=True, help="Path to source markdown")
    sa.add_argument("--meta",    required=True, help="Path to meta JSON")
    sa.add_argument("--no-git",  action="store_true", help="Skip git add/commit/push")
    sa.set_defaults(func=cmd_add)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
