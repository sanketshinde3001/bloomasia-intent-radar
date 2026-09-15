"""Scrape Bloomasia's live event catalogue (bloomasia.in) — the demo's entry point.

The acceptance test Azhar set is literally "go to bloomasia.in events, choose one,
show me intent leads for it", so the picker is driven by the real site.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .config import USER_AGENT
from .store import cache_get, cache_put

HOME = "https://www.bloomasia.in/"
_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}

_DATE_RX = re.compile(
    r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}"
    r"(?:\s*[-–&]\s*[^\n]{0,80})?)",
    re.I,
)

# Fallback catalogue (captured 14 Sep 2026) in case the site is down during a demo.
_FALLBACK = [
    ("Understanding Derivatives || Mastering ISDA Documentation", "https://bloomasia.in/understanding-derivatives-mastering-isda-documentation-recent-developmentsand-updates-in-otc-derivatives-and-isda-documentation/"),
    ("Mastering Labour Codes & Key Labour Law Compliances", "https://bloomasia.in/labour-codes-key-labour-law-compliances/"),
    ("Demystifying SEBI (LODR) Regulations & Insider Trading Regulations", "https://bloomasia.in/demystifying-sebi-lodr-regulations-insider-trading-regulations/"),
    ("Master Class on Board Governance & Corporate Compliance", "https://bloomasia.in/master-class-on-board-governance-corporate-compliance/"),
    ("New Labour Codes Workshop", "https://bloomasia.in/new-labour-code-workshop/"),
    ("Managing EPC Contracts under FIDIC & Model NITI Aayog", "https://bloomasia.in/managing-epc-contracts-under-fidic-model-niti-aayog/"),
    ("Advance Electrical Safety Training Master Class", "https://bloomasia.in/advance-electrical-safety-training-master-class/"),
    ("RBI's Regulatory Compliance on Fraud Risk Framework & Investigation Strategies", "https://bloomasia.in/rbis-regulatory-compliance-on-fraud-risk-framework-investigation-strategies/"),
    ("Mastering Related Party Transactions: Compliance, Best Practices & Practical Challenges", "https://bloomasia.in/mastering-related-party-transactions-compliance-best-practices-practical-challenges/"),
    ("Mastering AI for HR: Talent Strategies for AI-Augmented Organizations", "https://bloomasia.in/mastering-ai-for-hr-talent-strategies-for-ai-augmented-organizations/"),
    ("Contract Labour Act, ID Act & Wage Code", "https://bloomasia.in/contract-labour-act-id-act-wage-code/"),
    ("Digital Personal Data Protection (DPDP) Act", "https://bloomasia.in/dpdp-act/"),
    ("Tendering, Negotiation and Contract Drafting", "https://bloomasia.in/tendering-negotiation-and-contract-drafting/"),
    ("FIDIC Contracts Workshop", "https://bloomasia.in/fidic-contracts/"),
    ("Advanced BESS Training Program", "https://bloomasia.in/advanced-bess-training-program/"),
    ("Risk Management in Construction Contracts and Disputes", "https://bloomasia.in/risk-management-in-construction-contracts-and-disputes/"),
    ("Understanding the Financial & HR Implications of India's Four Labour Codes", "https://bloomasia.in/understanding-the-financial-hr-implications-of-indias-four-labour-codes/"),
    ("Advanced Contract Drafting and Dispute Management Programme for Infrastructure", "https://bloomasia.in/advanced-contract-drafting-and-dispute-management-programme-for-infrastructure/"),
]


def _slug(url: str) -> str:
    return urlparse(url).path.strip("/").split("/")[-1] or "home"


def list_events(force: bool = False) -> list[dict]:
    """Return [{id, title, url}] from the bloomasia.in homepage (cached 6h)."""
    cached = cache_get("events", "catalogue")
    if cached and not force and time.time() - cached.get("ts", 0) < 6 * 3600:
        return cached["events"]

    events: list[dict] = []
    try:
        r = httpx.get(HOME, headers=_HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        seen: set[str] = set()
        for a in soup.select("a.elementskit-btn[href]"):
            url = a["href"].strip()
            if "bloomasia" not in url or url in seen:
                continue
            body = a.find_parent(class_="elementskit-box-body") or a.parent
            title = body.get_text(" ", strip=True).replace("Read more", "").strip(" -|")
            title = re.sub(r"\s+", " ", title)
            if not title:
                continue
            seen.add(url)
            events.append({"id": _slug(url), "title": title, "url": url})
    except Exception:
        events = []

    if not events:
        events = [{"id": _slug(u), "title": t, "url": u} for t, u in _FALLBACK]

    # de-duplicate repeated listings (same title, different city/date pages)
    dedup: dict[str, dict] = {}
    for e in events:
        dedup.setdefault(e["id"], e)
    events = list(dedup.values())

    cache_put("events", "catalogue", {"ts": time.time(), "events": events})
    return events


def get_event(event_id: str) -> dict | None:
    for e in list_events():
        if e["id"] == event_id:
            return e
    return None


def fetch_event_details(event: dict) -> dict:
    """Fetch the event page → {title, url, date_venue, description}. Cached."""
    hit = cache_get("events", event["id"])
    if hit:
        return hit

    details = {**event, "date_venue": "", "description": ""}
    try:
        r = httpx.get(event["url"], headers=_HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = (soup.select_one("main") or soup.body or soup).get_text("\n", strip=True)
        text = re.sub(r"\n{2,}", "\n", text)
        m = _DATE_RX.search(text)
        if m:
            details["date_venue"] = m.group(1).strip()
        # Drop the site chrome that precedes the title, keep the first ~3500 chars
        idx = text.lower().find(event["title"].lower()[:25])
        if idx > 0:
            text = text[idx:]
        details["description"] = text[:3500]
    except Exception as exc:  # site down → still usable with title only
        details["description"] = f"(event page unavailable: {exc})"

    cache_put("events", event["id"], details)
    return details
