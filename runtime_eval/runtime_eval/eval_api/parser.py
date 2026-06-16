"""Document parsing: turn an uploaded file into plain text + sections.

Text formats (.md/.markdown/.txt/.text) decode directly. ``.chm`` (Microsoft
Compiled HTML Help) is extracted and converted to markdown by reusing the
mining pipeline's stdlib-only converter (loaded by file path so we avoid the
``knowledge_mining.mining.ingestion`` package import chain and its PDF deps).
Remaining binary formats (.pdf/.docx/.doc/.pptx/.xlsx) raise a clear error and
are left as an extension point.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}
BINARY_SUFFIXES = {".pdf", ".docx", ".doc", ".pptx", ".xlsx"}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# knowledge_mining/mining/ingestion/preprocessing.py relative to this file:
# parser.py -> eval_api -> runtime_eval(pkg) -> runtime_eval(dir) -> repo root.
_MINING_PREPROCESSING = (
    Path(__file__).resolve().parents[3]
    / "knowledge_mining"
    / "mining"
    / "ingestion"
    / "preprocessing.py"
)


class UnsupportedDocument(ValueError):
    """Raised when an uploaded document is not text-readable yet."""


def _suffix(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def decode_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


@lru_cache(maxsize=1)
def _archive_to_markdown():
    """Load mining's ``archive_to_markdown`` by file path.

    The module is stdlib-only and self-contained, so loading it directly avoids
    triggering ``knowledge_mining.mining.ingestion.__init__`` (which pulls in PDF
    deps). Returns the callable, or ``None`` if the source file is unavailable.
    """

    if not _MINING_PREPROCESSING.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "runtime_eval._mining_preprocessing", _MINING_PREPROCESSING
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "archive_to_markdown", None)


def chm_to_markdown(name: str, data: bytes) -> str:
    """Extract a ``.chm`` upload into markdown text.

    Requires Windows ``hh.exe`` or ``7z`` on PATH (used by the mining converter).
    Raises ``UnsupportedDocument`` (→ HTTP 415) when the converter or extractor
    is unavailable, or when extraction yields nothing.
    """

    converter = _archive_to_markdown()
    if converter is None:
        raise UnsupportedDocument(
            "无法解析 .chm：未找到 CHM 转换器"
            "（knowledge_mining 预处理模块缺失）。"
        )
    work = Path(tempfile.mkdtemp(prefix="eval_chm_"))
    src = work / "input.chm"  # force .chm suffix so the converter dispatches
    try:
        src.write_bytes(data)
        try:
            text = converter(src)
        except Exception as exc:  # extractor missing / extraction failed
            raise UnsupportedDocument(
                f"解析 .chm 失败：{exc}"
                "（需要 Windows hh.exe 或 PATH 上的 7-Zip）。"
            ) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not text.strip():
        raise UnsupportedDocument("解析 .chm 得到空内容。")
    return text


def split_sections(text: str) -> list[dict]:
    """Split markdown-ish text into [{title, text}] by ATX headings.

    Plain text with no headings yields a single untitled section.
    """

    sections: list[dict] = []
    current_title: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or current_title:
            sections.append({"title": current_title, "text": body})

    for line in text.splitlines():
        m = _HEADING_RE.match(line.strip())
        if m:
            flush()
            current_title = m.group(2).strip() or None
            buffer = []
        else:
            buffer.append(line)
    flush()

    if not sections:
        sections.append({"title": None, "text": text.strip()})
    return sections


def parse_document(
    *,
    name: str,
    data: bytes,
    max_chars: int | None = None,
) -> dict:
    """Parse raw uploaded bytes into ``{text, sections, char_count}``.

    Raises ``UnsupportedDocument`` for binary formats we cannot read yet.
    """

    suffix = _suffix(name)
    if suffix == ".chm":
        text = chm_to_markdown(name, data)
    elif suffix in BINARY_SUFFIXES:
        raise UnsupportedDocument(
            f"暂不支持二进制文档解析（{suffix}）：请先转换为 .md/.txt 再上传。"
        )
    else:
        # Text formats decode directly; unknown extensions are best-effort text.
        text = decode_bytes(data)

    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]
    sections = split_sections(text)
    return {"text": text, "sections": sections, "char_count": len(text)}
