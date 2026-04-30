"""CLI: Convert an extracted CHM/HDX folder into a single Markdown file.

This is now a thin wrapper around
`knowledge_mining.mining.ingestion.preprocessing.convert_extracted` so there
is a single source of truth for the HTML→MD logic. The mining ingestion
pipeline uses the same converter automatically for `.chm`/`.hdx` inputs.

Usage:
    python scripts/data/convert_html_to_md.py <extracted_dir> [-o output.md]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from knowledge_mining.mining.ingestion.preprocessing import (  # noqa: E402
    convert_chm_extracted,
    convert_hdx_extracted,
    detect_layout,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert an extracted CHM/HDX folder into a single Markdown file."
    )
    parser.add_argument("extracted_dir", type=Path,
                        help="Folder produced by extract_doc_archive.py")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output .md path (default: <parent>/<dirname>.md).")
    parser.add_argument("--title", type=str, default=None,
                        help="Document title for the top-level H1 (default: folder name).")
    parser.add_argument("--layout", choices=["auto", "chm", "hdx"], default="auto",
                        help="Force a layout instead of auto-detection.")
    args = parser.parse_args(argv)

    src: Path = args.extracted_dir
    if not src.is_dir():
        print(f"Not a directory: {src}", file=sys.stderr)
        return 2

    layout = args.layout if args.layout != "auto" else detect_layout(src)
    output: Path = args.output or (src.parent / f"{src.name}.md")

    if layout == "chm":
        md, stats = convert_chm_extracted(src, args.title)
    else:
        md, stats = convert_hdx_extracted(src, args.title)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")

    print(f"Layout: {layout}")
    print(f"TOC entries: {stats['toc_entries']}")
    print(f"Converted: {stats['converted']}")
    if stats.get("missing"):
        print(f"Missing topics: {stats['missing']}")
    print(f"Output: {output}  ({output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
