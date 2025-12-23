"""Ephemeral PDF-tools job storage with TTL-based cleanup."""

from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from services.settings import settings_manager


PDF_JOB_TTL_SECONDS = 300  # 5 minutes after completion


@dataclass(frozen=True)
class PdfJobPaths:
    root: Path
    job_dir: Path
    input_dir: Path
    output_dir: Path
    meta_file: Path


def jobs_root() -> Path:
    return settings_manager.paths.config_dir / "pdf_jobs"


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_job_paths(job_id: str) -> PdfJobPaths:
    root = jobs_root()
    job_dir = root / job_id
    return PdfJobPaths(
        root=root,
        job_dir=job_dir,
        input_dir=job_dir / "input",
        output_dir=job_dir / "output",
        meta_file=job_dir / "meta.json",
    )


def create_job(*, tool: str, owner_user_id: int) -> str:
    job_id = secrets.token_urlsafe(16)
    paths = build_job_paths(job_id)
    paths.input_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    meta: Dict[str, Any] = {
        "job_id": job_id,
        "tool": tool,
        "owner_user_id": int(owner_user_id),
        "created_at": now,
        "updated_at": now,
        "state": "created",
        "percent": 0,
        "stage": "",
        "message": "",
        "completed_at": None,
        "expires_at": None,
        "result_filename": None,
        "error": None,
        "cancel_requested": False,
    }
    _atomic_write_json(paths.meta_file, meta)
    return job_id


def get_job_meta(job_id: str) -> Dict[str, Any]:
    paths = build_job_paths(job_id)
    return _read_json(paths.meta_file)


def update_job_meta(job_id: str, **updates: Any) -> Dict[str, Any]:
    paths = build_job_paths(job_id)
    meta = _read_json(paths.meta_file)
    if bool(meta.get("cancel_requested")):
        allowed = {"cancel_requested", "completed_at", "expires_at"}
        for key, value in updates.items():
            if key in allowed:
                meta[key] = value
    else:
        meta.update(updates)
    meta["updated_at"] = time.time()
    _atomic_write_json(paths.meta_file, meta)
    return meta


def set_job_status(
    job_id: str,
    *,
    state: str,
    percent: int,
    stage: str = "",
    message: str = "",
    error: Optional[str] = None,
    result_filename: Optional[str] = None,
) -> Dict[str, Any]:
    percent_clamped = max(0, min(int(percent), 100))
    updates: Dict[str, Any] = {
        "state": state,
        "percent": percent_clamped,
        "stage": stage or "",
        "message": message or "",
    }
    if error is not None:
        updates["error"] = error
    if result_filename is not None:
        updates["result_filename"] = result_filename
    return update_job_meta(job_id, **updates)


def mark_job_completed(job_id: str) -> Dict[str, Any]:
    paths = build_job_paths(job_id)
    meta = _read_json(paths.meta_file)
    if meta.get("completed_at") and meta.get("expires_at"):
        return meta
    now = time.time()
    updated = update_job_meta(
        job_id,
        completed_at=meta.get("completed_at") or now,
        expires_at=meta.get("expires_at") or (now + PDF_JOB_TTL_SECONDS),
    )
    _schedule_expired_cleanup()
    return updated


def _schedule_expired_cleanup() -> None:
    def _run() -> None:
        try:
            cleanup_expired_jobs()
        except Exception:
            pass

    timer = threading.Timer(PDF_JOB_TTL_SECONDS + 1, _run)
    timer.daemon = True
    timer.start()


def assert_job_owner(job_meta: Dict[str, Any], *, owner_user_id: int) -> None:
    if int(job_meta.get("owner_user_id") or 0) != int(owner_user_id):
        raise PermissionError("Job not found")


def cleanup_expired_jobs(*, now: Optional[float] = None) -> int:
    root = jobs_root()
    if not root.exists():
        return 0

    now_ts = time.time() if now is None else float(now)
    removed = 0

    for job_dir in root.iterdir():
        if not job_dir.is_dir():
            continue
        meta_file = job_dir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = _read_json(meta_file)
        except Exception:
            continue

        state = (meta.get("state") or "").lower()
        expires_at = meta.get("expires_at")
        if not expires_at:
            continue
        try:
            expires_at_ts = float(expires_at)
        except (TypeError, ValueError):
            continue

        if state in {"processing", "awaiting_order", "awaiting_signature_params", "created", "queued"}:
            continue
        if now_ts < expires_at_ts:
            continue

        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            removed += 1
        except Exception:
            continue

    return removed
