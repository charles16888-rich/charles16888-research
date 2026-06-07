"""
update_manifest.py
==================
CRUD for manifest.json — the website's single source of truth for what
content exists.

Subcommands:
    add        Append (or replace by id) a single entry.
    remove     Delete an entry by id.
    market     Update the global market_summary block.
    rebuild    Recompute `updated_at` and re-sort entries.
    show       Print the manifest summary for sanity.

The script keeps the manifest valid: it preserves schema_version, normalises
the entries list, and updates `updated_at` on every write.

Usage examples:
    python tools/update_manifest.py add --meta entry.json
    python tools/update_manifest.py remove --id 2026-05-22-sectors-daily
    python tools/update_manifest.py market --json '{"taiex":22847,"change_pct":1.21}'
    python tools/update_manifest.py rebuild
    python tools/update_manifest.py show
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


WEBSITE_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = WEBSITE_ROOT / "manifest.json"

TPE = ZoneInfo("Asia/Taipei")


# ── IO helpers ────────────────────────────────────────────────────────────────

def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {
            "schema_version": 1,
            "updated_at": now_iso(),
            "today": None,
            "volume_number": 1,
            "market_summary": {},
            "entries": [],
        }
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(m: dict) -> None:
    m["updated_at"] = now_iso()
    MANIFEST_PATH.write_text(
        json.dumps(m, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def now_iso() -> str:
    return _dt.datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def sort_entries(entries: list[dict]) -> list[dict]:
    return sorted(
        entries,
        key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")),
        reverse=True,
    )


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_add(args) -> int:
    meta_path = Path(args.meta)
    if not meta_path.exists():
        print(f"[ERR] meta json not found: {meta_path}", file=sys.stderr)
        return 1

    entry = json.loads(meta_path.read_text(encoding="utf-8"))
    if "id" not in entry:
        # Build id from date + category + type if not provided
        entry["id"] = "-".join(filter(None, [
            entry.get("date", ""),
            entry.get("category", ""),
            entry.get("type", ""),
        ]))

    m = load_manifest()

    # Replace if id exists, else append
    new_entries = [e for e in m.get("entries", []) if e.get("id") != entry["id"]]
    new_entries.append(entry)
    m["entries"] = sort_entries(new_entries)

    # Update `today` to the latest entry date
    if m["entries"]:
        m["today"] = m["entries"][0]["date"]

    save_manifest(m)
    print(f"[OK] added/updated entry: {entry['id']}")
    return 0


def cmd_remove(args) -> int:
    m = load_manifest()
    before = len(m.get("entries", []))
    m["entries"] = [e for e in m.get("entries", []) if e.get("id") != args.id]
    after = len(m["entries"])
    if before == after:
        print(f"[NOOP] no entry with id={args.id}", file=sys.stderr)
        return 1
    save_manifest(m)
    print(f"[OK] removed entry: {args.id}")
    return 0


def cmd_market(args) -> int:
    m = load_manifest()
    if args.json:
        patch = json.loads(args.json)
    elif args.json_file:
        patch = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    else:
        print("[ERR] need --json or --json-file", file=sys.stderr)
        return 1

    m.setdefault("market_summary", {}).update(patch)
    save_manifest(m)
    print(f"[OK] market_summary updated with {len(patch)} fields")
    return 0


def cmd_rebuild(args) -> int:
    m = load_manifest()
    m["entries"] = sort_entries(m.get("entries", []))
    if m["entries"]:
        m["today"] = m["entries"][0]["date"]
    save_manifest(m)
    print(f"[OK] rebuilt: {len(m['entries'])} entries, today={m.get('today')}")
    return 0


def cmd_show(args) -> int:
    m = load_manifest()
    print(f"schema_version: {m.get('schema_version')}")
    print(f"updated_at:     {m.get('updated_at')}")
    print(f"today:          {m.get('today')}")
    print(f"volume_number:  {m.get('volume_number')}")
    print(f"entries:        {len(m.get('entries', []))}")
    # Count per category
    by_cat = {}
    for e in m.get("entries", []):
        by_cat[e.get("category", "?")] = by_cat.get(e.get("category", "?"), 0) + 1
    for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat:12s} {count}")
    return 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Manage manifest.json entries.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sa = sub.add_parser("add", help="Add or replace an entry from a meta JSON file.")
    sa.add_argument("--meta", required=True, help="Path to entry JSON")
    sa.set_defaults(func=cmd_add)

    sr = sub.add_parser("remove", help="Remove an entry by id.")
    sr.add_argument("--id", required=True)
    sr.set_defaults(func=cmd_remove)

    sm = sub.add_parser("market", help="Update market_summary fields.")
    sm.add_argument("--json", help="Inline JSON patch (e.g. '{\"taiex\":22847}')")
    sm.add_argument("--json-file", help="Path to JSON patch file")
    sm.set_defaults(func=cmd_market)

    sb = sub.add_parser("rebuild", help="Re-sort entries and refresh updated_at.")
    sb.set_defaults(func=cmd_rebuild)

    ss = sub.add_parser("show", help="Print manifest summary.")
    ss.set_defaults(func=cmd_show)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
