"""
wrap_report.py
==============
Convert a Markdown report (e.g. industry_map's daily/weekly/rotation/focus .md
output) into a styled HTML page wrapped with the editorial frame.

Usage (one-shot CLI):
    python tools/wrap_report.py \
        --input  E:/industry_map/reports/daily_2026-05-22.md \
        --output E:/industry_map/website/reports/2026-05-22/sectors-daily.html \
        --category-id sectors \
        --category-name 族群 \
        --type-id daily \
        --type-label "盤後快報" \
        --date 2026-05-22 \
        --time 20:00 \
        --title "面板族群暴衝 9.87%，ABF 載板續強" \
        --title-em "暴衝" \
        --lead "盤後 74 個族群中 53 個收紅..." \
        --volume 1 \
        --asset-prefix "../../" \
        --stats '[{"label":"領漲","value":"面板 +9.87%","color":"up"}]'

Usage (programmatic):
    from wrap_report import wrap_report
    wrap_report(input_md, output_html, meta_dict)

Requires:
    pip install markdown
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

try:
    import markdown as md_lib
except ImportError:
    print("ERROR: the 'markdown' package is required. Install with: pip install markdown", file=sys.stderr)
    sys.exit(2)


# Path to the wrap template, resolved relative to this script.
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "report-wrap.html"

# ── Regex helpers ─────────────────────────────────────────────────────────────

# Catches a broad swath of decorative emoji + enclosed alphanumerics that show
# up in industry_map's markdown headers (📊 🌐 🟢 🔴 🚀 ❶ ❷ etc.).
DECORATION_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # symbols & pictographs, transport, supplemental
    "☀-➿"            # misc symbols + dingbats
    "①-⓿"            # enclosed alphanumerics (❶❷ etc.)
    "⬀-⯿"
    "〰-〿"
    "︀-️"
    "]"
)

# Highlight title_em substring inside title (case-sensitive, first match)
def emphasize_title(title: str, title_em: str | None) -> str:
    if not title_em:
        return html.escape(title)
    idx = title.find(title_em)
    if idx < 0:
        return html.escape(title)
    return (
        html.escape(title[:idx])
        + f"<em>{html.escape(title_em)}</em>"
        + html.escape(title[idx + len(title_em):])
    )


# ── Markdown preprocessing ────────────────────────────────────────────────────

def strip_decoration(text: str) -> str:
    """Remove emoji / enclosed glyphs; collapse the whitespace they leave."""
    text = DECORATION_RE.sub("", text)
    # Collapse runs of spaces that the removal created (but keep newlines).
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Trim leading spaces on each line that resulted from a leading emoji.
    text = re.sub(r"^[ \t]+", "", text, flags=re.MULTILINE)
    return text


def remove_first_h1(text: str) -> tuple[str, str | None]:
    """Strip the document's first H1; return (remaining_md, h1_text_or_None)."""
    lines = text.splitlines()
    out: list[str] = []
    h1: str | None = None
    for line in lines:
        if h1 is None and line.lstrip().startswith("# ") and not line.lstrip().startswith("## "):
            h1 = line.lstrip()[2:].strip()
            continue
        out.append(line)
    return "\n".join(out), h1


# ── HTML postprocessing ───────────────────────────────────────────────────────

def add_classes(html_body: str) -> str:
    """Augment the rendered HTML with our editorial CSS hooks."""
    # Apply drop-cap only when the first paragraph has enough narrative text
    # (>= 60 chars of plain text). Short metadata-style opening paragraphs
    # like "共 74 個族群｜總成交額 14051.0 億" would otherwise leave a giant
    # solitary first letter colliding with the next heading.
    first_p = re.match(r"\s*<p>(.*?)</p>", html_body, re.DOTALL)
    if first_p:
        plain = re.sub(r"<[^>]+>", "", first_p.group(1)).strip()
        if len(plain) >= 60:
            html_body = re.sub(
                r"^\s*<p>",
                '<p class="drop-cap">',
                html_body,
                count=1,
            )
    # Tables → data-table style. (The .report-body CSS already styles bare
    # tables; this adds an explicit hook for the future.)
    html_body = html_body.replace("<table>", '<table class="data-table">')
    # Right-align cells whose text content is mostly numeric (and add class="num").
    # Conservative: only flag obvious pure-numeric cells with possible % sign.
    html_body = re.sub(
        r"<td>([+\-−]?\d[\d,\.]*\s*[%億千萬]?)</td>",
        r'<td class="num">\1</td>',
        html_body,
    )
    return html_body


# ── Template fill ─────────────────────────────────────────────────────────────

def render_stats(stats: list[dict]) -> str:
    if not stats:
        return ""
    cells = []
    for s in stats:
        color = s.get("color", "neutral")
        color_cls = {"up": "num-up", "down": "num-down"}.get(color, "num-neutral")
        cells.append(f"""
        <div class="stat">
          <div class="stat__label">{html.escape(str(s.get("label", "")))}</div>
          <div class="stat__value stat__value--sm {color_cls}">{html.escape(str(s.get("value", "")))}</div>
        </div>""")
    cls = "stat-row stat-row--3" if len(stats) == 3 else (
        "stat-row stat-row--5" if len(stats) == 5 else "stat-row"
    )
    return f'<div class="{cls}">{"".join(cells)}</div>'


def render_lead(lead: str | None) -> str:
    if not lead:
        return ""
    return f'<p class="report-lead">{html.escape(lead)}</p>'


def fill_template(template: str, ctx: dict) -> str:
    return re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: str(ctx.get(m.group(1), "")),
        template,
    )


# ── Main wrap function ───────────────────────────────────────────────────────

def wrap_report(input_md: Path, output_html: Path, meta: dict) -> None:
    if meta.get("content_html") is not None:
        body_html = str(meta.get("content_html") or "")
    else:
        raw = Path(input_md).read_text(encoding="utf-8")

        # Strip decorations + first H1.
        cleaned = strip_decoration(raw)
        body_md, _h1 = remove_first_h1(cleaned)

        # Convert markdown.
        md = md_lib.Markdown(
            # md_in_html lets us write <details markdown="1">...</details> blocks
            # in the source markdown and still have inner markdown processed.
            extensions=["tables", "fenced_code", "attr_list", "sane_lists", "md_in_html"],
            output_format="html5",
        )
        body_html = md.convert(body_md)
        body_html = add_classes(body_html)

    # Build template context.
    time_str = meta.get("time") or ""
    ctx = {
        "TITLE": html.escape(meta.get("title", "")),
        "ASSET_PREFIX": meta.get("asset_prefix", "../../"),
        "CATEGORY_ID": html.escape(meta.get("category_id", "sectors")),
        "CATEGORY_NAME": html.escape(meta.get("category_name", "族群")),
        "TYPE_LABEL": html.escape(meta.get("type_label", "")),
        "DATE": html.escape(meta.get("date", "")),
        "TIME_SPACED": f" · {html.escape(time_str)}" if time_str else "",
        "VOLUME": str(meta.get("volume", "01")).zfill(2),
        "HEADLINE_HTML": emphasize_title(meta.get("title", ""), meta.get("title_em")),
        "LEAD_HTML": render_lead(meta.get("lead")),
        "STATS_HTML": render_stats(meta.get("stats", [])),
        "CONTENT_HTML": body_html,
        "PREV_NEXT_HTML": meta.get("prev_next_html", ""),
    }

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = fill_template(template, ctx)

    output_html = Path(output_html)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(rendered, encoding="utf-8")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Convert a markdown report to a wrapped HTML page.")
    p.add_argument("--input", required=True, help="Path to input .md")
    p.add_argument("--output", required=True, help="Path to output .html")
    p.add_argument("--meta-json", help="JSON file containing all meta fields (overrides CLI flags)")
    p.add_argument("--category-id", default="sectors")
    p.add_argument("--category-name", default="族群")
    p.add_argument("--type-id", default="daily")
    p.add_argument("--type-label", default="")
    p.add_argument("--date", default="")
    p.add_argument("--time", default="")
    p.add_argument("--title", default="")
    p.add_argument("--title-em", default="")
    p.add_argument("--lead", default="")
    p.add_argument("--volume", default="01")
    p.add_argument("--asset-prefix", default="../../")
    p.add_argument("--stats", default="[]", help='JSON list of {label,value,color}')
    args = p.parse_args()

    if args.meta_json:
        meta = json.loads(Path(args.meta_json).read_text(encoding="utf-8"))
    else:
        meta = {
            "category_id":   args.category_id,
            "category_name": args.category_name,
            "type_id":       args.type_id,
            "type_label":    args.type_label,
            "date":          args.date,
            "time":          args.time,
            "title":         args.title,
            "title_em":      args.title_em,
            "lead":          args.lead,
            "volume":        args.volume,
            "asset_prefix":  args.asset_prefix,
            "stats":         json.loads(args.stats),
        }

    wrap_report(Path(args.input), Path(args.output), meta)
    print(f"✓ wrote {args.output}")


if __name__ == "__main__":
    main()
