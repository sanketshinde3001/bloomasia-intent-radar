"""Clay adapter (optional, recommended — Bloomasia can run on BFWAI's Clay workspace).

Setup (10 min, one-time):
  1. In Clay create a table with a Webhook source; copy the webhook URL → CLAY_WEBHOOK_URL.
  2. Columns arrive as: company, company_key, titles (comma-sep), cities, event, job_id, callback_url.
  3. Add "Find People at Company" (titles/locations from the row) → "Enrich Work Email" waterfall →
     "Enrich Phone" (optional).
  4. Add an "HTTP API" enrichment: POST {callback_url} with JSON
        {"secret": "<CLAY_CALLBACK_SECRET>", "job_id": "{{job_id}}", "company_key": "{{company_key}}",
         "contacts": [{"name":..,"title":..,"linkedin_url":..,"email":..,"email_status":..,"phone":..}]}
  The app stores the contacts and the dashboard shows them on refresh.
"""
from __future__ import annotations

import httpx

from .. import config


def available() -> bool:
    return bool(config.CLAY_WEBHOOK_URL)


def request(account: dict, icp: dict, callback_url: str = "http://localhost:8000/api/clay/callback") -> None:
    payload = {
        "company": account["company"],
        "domain": account.get("domain", ""),
        "company_key": account["company_key"],
        "titles": ", ".join(icp.get("buyer_titles", [])[:8]),
        "cities": ", ".join(icp.get("cities", [])[:5]),
        "event": icp.get("topic", ""),
        "job_id": account.get("job_id", ""),
        "callback_url": callback_url,
    }
    r = httpx.post(config.CLAY_WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()
