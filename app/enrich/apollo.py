"""Apollo.io adapter (optional). Needs a plan with API access to people search."""
from __future__ import annotations

import httpx

from .. import config

BASE = "https://api.apollo.io/api/v1"


def available() -> bool:
    return bool(config.APOLLO_API_KEY)


def _headers():
    return {"x-api-key": config.APOLLO_API_KEY, "Content-Type": "application/json", "Cache-Control": "no-cache"}


def find_people(company: str, titles: list[str], limit: int | None = None, domain: str = "") -> list[dict]:
    limit = limit or config.CONTACTS_PER_ACCOUNT
    payload = {
        "q_organization_name": company,
        **({"q_organization_domains_list": [domain]} if domain else {}),
        "person_titles": titles[:10],
        "person_locations": ["India"],
        "page": 1,
        "per_page": max(limit * 2, 5),
    }
    r = httpx.post(f"{BASE}/mixed_people/search", json=payload, headers=_headers(), timeout=40)
    r.raise_for_status()
    people = r.json().get("people", [])
    out: list[dict] = []
    for p in people:
        org = (p.get("organization") or {}).get("name", "")
        if org and company.lower().split()[0] not in org.lower():
            continue  # fuzzy guard against wrong-company matches
        out.append(
            {
                "name": p.get("name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                "title": p.get("title", ""),
                "company": org or company,
                "linkedin_url": p.get("linkedin_url", ""),
                "email": p.get("email") or "",
                "email_status": p.get("email_status") or ("available" if p.get("email") else "locked"),
                "phone": (p.get("phone_numbers") or [{}])[0].get("sanitized_number", "") if p.get("phone_numbers") else "",
                "city": p.get("city", ""),
                "source": "apollo",
                "apollo_id": p.get("id"),
            }
        )
        if len(out) >= limit:
            break
    return out


def reveal(apollo_id: str) -> dict:
    """Spend a credit to reveal email/phone for one person (people/match)."""
    r = httpx.post(f"{BASE}/people/match", json={"id": apollo_id, "reveal_personal_emails": False}, headers=_headers(), timeout=40)
    r.raise_for_status()
    p = r.json().get("person", {})
    return {"email": p.get("email", ""), "phone": (p.get("phone_numbers") or [{}])[0].get("sanitized_number", "") if p.get("phone_numbers") else ""}
