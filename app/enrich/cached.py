from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import CONTACTS_DIR
from ..signals.base import normalize_company


def _path(company_key: str):
    safe = "".join(c if c.isalnum() else "_" for c in company_key)[:80]
    return CONTACTS_DIR / f"{safe}.json"


def lookup(company_key: str) -> list[dict]:
    p = _path(company_key)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("contacts", [])
    except Exception:
        return []


def save(company_key: str, contacts: list[dict], source: str = "manual") -> None:
    p = _path(company_key)
    p.write_text(
        json.dumps(
            {"company_key": company_key, "source": source, "saved_at": datetime.now(timezone.utc).isoformat(), "contacts": contacts},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def save_for_company(company_name: str, contacts: list[dict], source: str = "manual") -> str:
    key = normalize_company(company_name)
    save(key, contacts, source)
    return key
