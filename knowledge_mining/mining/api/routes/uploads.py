"""File upload routes — multipart upload with domain-scoped storage."""
from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

UPLOAD_ROOT = Path(os.environ.get("UPLOAD_ROOT", "./uploads"))
MAX_FILES_PER_REQUEST = 100
MAX_FILE_SIZE = int(os.environ.get("UPLOAD_MAX_FILE_SIZE", 100 * 1024 * 1024))  # 100MB default

_DOMAIN_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


def _validate_domain(domain: str) -> str:
    """Sanitize domain: only allow alphanumeric, underscore, hyphen."""
    if not _DOMAIN_PATTERN.fullmatch(domain):
        raise HTTPException(400, "Invalid domain name: only alphanumeric, underscore, hyphen allowed")
    return domain


@router.post("")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    domain: str = Form("cloud_core_network"),
) -> dict[str, Any]:
    """Upload files to a domain-scoped batch directory.

    Returns upload_batch_id and storage_path (usable as input_path for POST /api/runs).
    """
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(400, f"Too many files: max {MAX_FILES_PER_REQUEST}")

    domain = _validate_domain(domain)

    batch_id = uuid.uuid4().hex[:12]
    dest_dir = UPLOAD_ROOT / domain / batch_id

    # Belt-and-suspenders: ensure resolved path stays under UPLOAD_ROOT
    dest_dir = dest_dir.resolve()
    if not str(dest_dir).startswith(str(UPLOAD_ROOT.resolve())):
        raise HTTPException(400, "Invalid path")

    dest_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for f in files:
        if not f.filename:
            continue
        safe_name = Path(f.filename).name
        if not safe_name:
            continue
        file_path = dest_dir / safe_name

        # Chunked write with size limit
        total_size = 0
        with open(file_path, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    # Clean up partial file
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(400, f"File {safe_name} exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit")
                out.write(chunk)
        saved.append(safe_name)

    if not saved:
        raise HTTPException(400, "No valid files in upload")

    storage_path = str(dest_dir)

    logger.info("Uploaded %d files to %s (domain=%s)", len(saved), storage_path, domain)

    return {
        "upload_batch_id": batch_id,
        "domain": domain,
        "file_count": len(saved),
        "files": saved,
        "storage_path": storage_path,
    }


@router.get("")
async def list_uploads(
    request: Request,
    domain: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List upload batches from the filesystem."""
    if domain:
        domain = _validate_domain(domain)

    if not UPLOAD_ROOT.exists():
        return {"items": []}

    items: list[dict[str, Any]] = []

    scan_dirs = [UPLOAD_ROOT / domain] if domain else [
        d for d in UPLOAD_ROOT.iterdir() if d.is_dir()
    ]

    for domain_dir in scan_dirs:
        if not domain_dir.is_dir():
            continue
        domain_name = domain_dir.name
        for batch_dir in sorted(domain_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not batch_dir.is_dir():
                continue
            files = [f.name for f in batch_dir.iterdir() if f.is_file()]
            items.append({
                "upload_batch_id": batch_dir.name,
                "domain": domain_name,
                "file_count": len(files),
                "files": files,
                "storage_path": str(batch_dir),
            })
            if len(items) >= limit:
                return {"items": items}

    return {"items": items}
