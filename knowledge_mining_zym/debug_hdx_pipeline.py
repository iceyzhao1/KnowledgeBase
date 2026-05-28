"""Debug runner: dump every .hdx ingestion step to disk for inspection.

复刻 ingestion 对单个 .hdx 文件的完整处理链，并把每一步的中间产物落盘，
方便对照查看“从 .hdx 到最终 markdown”到底发生了什么、哪一步出了问题。

用法：
    py -3.10 knowledge_mining_zym/debug_hdx_pipeline.py <某文件.hdx> [--out 输出目录]

不指定 --out 时，默认在 .hdx 同级目录下创建 ./hdx_debug_<文件名>/。

输出目录结构：
    00_input.json            原始文件信息 + raw_content_hash
    01_extracted/            解压后的原始内容（zipfile.extractall 的结果，保留不删）
    02_layout.json           布局判定 + 主题文件的发现/过滤/最终排序
    03_topics/               每个主题 html 单独转换出的 markdown（NNN__<stem>.md）
    04_final.md              拼接后的最终 markdown（= pipeline 实际入库的 content）
    05_normalized.md         归一化后的文本（用于算 normalized_content_hash）
    manifest.json            汇总：哈希、计数、stats、推断标题、各步指针
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_mining_zym.mining.infra.hash_utils import (  # noqa: E402
    compute_raw_hash,
    compute_snapshot_hash,
    normalize_for_snapshot,
)
from knowledge_mining_zym.mining.ingestion import _infer_title  # noqa: E402
from knowledge_mining_zym.mining.ingestion.preprocessing import (  # noqa: E402
    SUPPORTED_ARCHIVE_EXTS,
    convert_topic,
    detect_layout,
    extract_archive,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj) -> None:
    _write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _list_tree(root: Path) -> list[dict]:
    """Flat listing of every file under root with relative path + byte size."""
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out.append({
                "path": str(p.relative_to(root)).replace("\\", "/"),
                "bytes": p.stat().st_size,
            })
    return out


def debug_hdx(hdx_path: Path, out_dir: Path) -> dict:
    ext = hdx_path.suffix.lower()
    if ext not in SUPPORTED_ARCHIVE_EXTS:
        raise SystemExit(f"Not a supported archive ({SUPPORTED_ARCHIVE_EXTS}): {hdx_path}")
    if not hdx_path.is_file():
        raise SystemExit(f"File not found: {hdx_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"input": str(hdx_path), "ext": ext, "steps": {}}

    # ── Step 0: raw bytes + raw hash (与 ingestion 完全一致) ──
    raw_bytes = hdx_path.read_bytes()
    raw_hash = compute_raw_hash(raw_bytes)
    step0 = {
        "file_name": hdx_path.name,
        "bytes": len(raw_bytes),
        "raw_content_hash": raw_hash,
        "is_zip": __import__("zipfile").is_zipfile(hdx_path),
    }
    _write_json(out_dir / "00_input.json", step0)
    manifest["steps"]["00_input"] = step0
    print(f"[00] raw bytes={len(raw_bytes)}  raw_hash={raw_hash[:16]}…  is_zip={step0['is_zip']}")

    # ── Step 1: extract (zipfile.extractall)，保留解压目录不删 ──
    extracted_dir = out_dir / "01_extracted"
    if extracted_dir.exists():
        import shutil
        shutil.rmtree(extracted_dir, ignore_errors=True)
    extract_archive(hdx_path, extracted_dir)
    tree = _list_tree(extracted_dir)
    _write_json(out_dir / "01_extracted_tree.json", tree)
    manifest["steps"]["01_extracted"] = {"dir": "01_extracted", "file_count": len(tree)}
    print(f"[01] extracted {len(tree)} files -> {extracted_dir}")

    # ── Step 2: layout detect + 主题文件发现/过滤/排序 (复刻 convert_hdx_extracted) ──
    try:
        layout = detect_layout(extracted_dir)
    except Exception as e:
        layout = f"<detect failed: {e}>"
    res_dir = extracted_dir / "resources"

    all_htmls = sorted(res_dir.glob("*.htm*")) if res_dir.is_dir() else []
    dropped = [p.name for p in all_htmls if p.stem.startswith("hedex-")]
    kept = [p for p in all_htmls if not p.stem.startswith("hedex-")]
    step2 = {
        "layout": layout,
        "resources_dir_exists": res_dir.is_dir(),
        "topics_discovered": [p.name for p in all_htmls],
        "topics_dropped_hedex_prefix": dropped,
        "topics_kept_in_order": [p.name for p in kept],
        "ordering_note": "字典序排序（HDX 无 TOC，顺序完全取决于文件名）",
    }
    _write_json(out_dir / "02_layout.json", step2)
    manifest["steps"]["02_layout"] = step2
    print(f"[02] layout={layout}  discovered={len(all_htmls)}  dropped={len(dropped)}  kept={len(kept)}")

    if not kept:
        raise SystemExit(f"No topic htmls under {res_dir} — 后续步骤无法进行。见 02_layout.json")

    # ── Step 3: 逐主题 HTML -> markdown ──
    topics_dir = out_dir / "03_topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    doc_title = hdx_path.stem  # ingestion 传入的就是 stem
    parts: list[str] = [f"# {doc_title}\n"]
    per_topic: list[dict] = []
    converted = 0
    for i, p in enumerate(kept):
        rec = {"index": i, "topic": p.name}
        try:
            md = convert_topic(p, depth=1, image_path_prefix="")
            out_name = f"{i:03d}__{p.stem}.md"
            _write(topics_dir / out_name, md)
            rec.update({"status": "ok", "out": f"03_topics/{out_name}", "md_chars": len(md), "empty": not bool(md)})
            if md:
                parts.append(md)
                converted += 1
        except Exception as e:
            rec.update({"status": "failed", "error": f"{type(e).__name__}: {e}"})
            print(f"[03] WARN topic failed: {p.name}: {e}", file=sys.stderr)
        per_topic.append(rec)
    _write_json(out_dir / "03_topics_index.json", per_topic)
    manifest["steps"]["03_topics"] = {"converted": converted, "total_kept": len(kept), "topics": per_topic}
    print(f"[03] converted {converted}/{len(kept)} topics -> {topics_dir}")

    # ── Step 4: 拼接最终 markdown（= pipeline 实际写入的 content） ──
    final_md = "\n\n".join(parts) + "\n"
    _write(out_dir / "04_final.md", final_md)
    manifest["steps"]["04_final"] = {
        "out": "04_final.md",
        "chars": len(final_md),
        "inferred_title": _infer_title(hdx_path, final_md, "markdown"),
    }
    print(f"[04] final markdown chars={len(final_md)} -> 04_final.md")

    # ── Step 5: 归一化 + normalized_content_hash（snapshot 复用边界） ──
    normalized = normalize_for_snapshot(final_md)
    norm_hash = compute_snapshot_hash(final_md)
    _write(out_dir / "05_normalized.md", normalized)
    manifest["steps"]["05_normalized"] = {
        "out": "05_normalized.md",
        "chars": len(normalized),
        "normalized_content_hash": norm_hash,
    }
    print(f"[05] normalized chars={len(normalized)}  normalized_hash={norm_hash[:16]}…")

    _write_json(out_dir / "manifest.json", manifest)
    print(f"\n完成。所有产物在: {out_dir}")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Dump every .hdx ingestion step to disk.")
    ap.add_argument("hdx", type=Path, help="待处理的 .hdx 文件路径")
    ap.add_argument("--out", type=Path, default=None, help="输出目录（默认 ./hdx_debug_<stem>/）")
    args = ap.parse_args()

    hdx_path = args.hdx.resolve()
    out_dir = (args.out or hdx_path.parent / f"hdx_debug_{hdx_path.stem}").resolve()

    try:
        debug_hdx(hdx_path, out_dir)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
