"""Tiny JSON persistence for jobs and caches (no DB needed for a demo)."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from .config import CACHE_DIR, JOBS_DIR

_lock = threading.Lock()


def _atomic_write(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


# ---- jobs -------------------------------------------------------------------

def save_job(job: dict) -> None:
    with _lock:
        _atomic_write(JOBS_DIR / f"{job['id']}.json", job)


def load_job(job_id: str) -> dict | None:
    p = JOBS_DIR / f"{job_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_jobs() -> list[dict]:
    out = []
    for p in sorted(JOBS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: j.get(k) for k in ("id", "event_title", "status", "created_at", "lead_count")})
        except Exception:
            continue
    return out


# ---- generic cache ----------------------------------------------------------

def cache_key(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


def cache_get(namespace: str, key: str) -> Any | None:
    p = CACHE_DIR / namespace / f"{key}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def cache_put(namespace: str, key: str, value: Any) -> None:
    d = CACHE_DIR / namespace
    d.mkdir(parents=True, exist_ok=True)
    _atomic_write(d / f"{key}.json", value)
