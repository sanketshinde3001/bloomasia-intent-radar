"""BSE corporate announcements → hard, dated, company-level triggers.

Sub-categories such as "Award of Order / Receipt of Order" (EPC/contracts topics) and
"Appointment of Company Secretary / Compliance Officer" (LODR/RPT topics — a brand-new
CS is the best possible delegate) are queried directly. Generic announcements are scanned
for keywords (penalty, arbitration, strike ...).
"""
from __future__ import annotations

import datetime as dt
import re

import httpx

from .. import config
from ..store import cache_get, cache_key, cache_put
from .base import Signal

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}
ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

SUBCAT_KIND = {
    "Award of Order / Receipt of Order": ("corporate_event", 0.75, "Just won a contract — new project, new claims exposure"),
    "Appointment of Company Secretary / Compliance Officer": ("job_change", 0.85, "New Company Secretary / Compliance Officer just appointed"),
    "Resignation of Company Secretary / Compliance Officer": ("job_change", 0.6, "Compliance Officer resigned — gap in compliance ownership"),
    "Change in Management": ("job_change", 0.5, "Senior management change announced"),
    "Change in Directorate": ("job_change", 0.45, "Board composition changed"),
    "Acquisition": ("corporate_event", 0.5, "Acquisition announced — integration & governance load"),
    "Raising of Funds": ("corporate_event", 0.45, "Fund raise — heightened disclosure obligations"),
    "Updates - Corporate Insolvency Resolution Process  (CIRP)": ("corporate_event", 0.7, "Under CIRP"),
    "Open Offer - Updates": ("corporate_event", 0.5, "Open offer in progress"),
    "Credit Rating": ("corporate_event", 0.3, "Credit rating action"),
}
GENERIC_SUBCATS = ["General", "Press Release / Media Release", "Outcome of Board Meeting"]
_APPOINTEE = re.compile(
    r"(?i:Mr|Mrs|Ms|Shri|Smt|Dr|CS|CA)\.?\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}?)\s+"
    r"(?i:as|to the post of|has been appointed|company secretary)\b"
)


def _appointee(text: str) -> str:
    """'Appointment of Mrs. Nikita Rateria as Company Secretary…' → 'Nikita Rateria'."""
    m = _APPOINTEE.search(text or "")
    if not m:
        return ""
    name = re.sub(r"\s+", " ", m.group(1)).strip(" .,")
    return name.title() if name.isupper() else name


def _query(cat: str, subcat: str, days: int, max_pages: int = 4) -> list[dict]:
    """BSE caps a request at ~30 days, so the look-back is walked in 30-day windows."""
    key = cache_key("bse", cat, subcat, days, max_pages, dt.date.today().isoformat())
    hit = cache_get("bse", key)
    if hit is not None:
        return hit
    rows: list[dict] = []
    seen: set[str] = set()
    end = dt.date.today()
    remaining = days
    while remaining > 0:
        span = min(30, remaining)
        start = end - dt.timedelta(days=span)
        for page in range(1, max_pages + 1):
            params = {
                "pageno": page, "strCat": cat, "strPrevDate": start.strftime("%Y%m%d"), "strScrip": "",
                "strSearch": "P", "strToDate": end.strftime("%Y%m%d"), "strType": "C", "subcategory": subcat,
            }
            try:
                r = httpx.get(API, params=params, headers=_HEADERS, timeout=25)
                if r.status_code != 200 or not r.text.startswith("{"):
                    break
                table = r.json().get("Table", [])
            except Exception:
                break
            if not table:
                break
            for x in table:
                nid = x.get("NEWSID") or f"{x.get('SCRIP_CD')}|{x.get('NEWS_DT')}|{x.get('HEADLINE')}"
                if nid in seen:
                    continue
                seen.add(nid)
                rows.append(
                    {
                        "company": re.sub(r"\s*-\s*\$$|\s*\$$", "", x.get("SLONGNAME") or "").strip(),
                        "headline": (x.get("HEADLINE") or x.get("NEWSSUB") or "").strip(),
                        "subcat": x.get("SUBCATNAME") or subcat,
                        "date": (x.get("NEWS_DT") or "")[:10],
                        "url": ATTACH + x["ATTACHMENTNAME"] if x.get("ATTACHMENTNAME") else "https://www.bseindia.com/corporates/ann.html",
                        "more": (x.get("MORE") or "")[:300],
                        "scrip_code": str(x.get("SCRIP_CD") or ""),
                    }
                )
            if len(table) < 50:
                break
        end = start - dt.timedelta(days=1)
        remaining -= span + 1
    cache_put("bse", key, rows)
    return rows


def collect(icp: dict, progress=None) -> list[Signal]:
    signals: list[Signal] = []
    days = min(config.LOOKBACK_DAYS, 60)

    for subcat in icp.get("bse_subcategories", []):
        kind, rel, reason = SUBCAT_KIND.get(subcat, ("corporate_event", 0.4, subcat))
        rows = _query("Company Update", subcat, days)
        if progress:
            progress(f"BSE '{subcat}': {len(rows)} announcements")
        for r in rows:
            if not r["company"]:
                continue
            person = _appointee(f"{r['headline']} {r['more']}") if "Appointment" in subcat else ""
            signals.append(
                Signal(
                    source="bse", kind=kind, company=r["company"], title=r["headline"][:200] or subcat,
                    url=r["url"], date=r["date"], snippet=r["more"], relevance=rel,
                    reason=(f"{person} appointed Company Secretary & Compliance Officer on {r['date']}" if person else reason),
                    person_name=person, person_title="Company Secretary & Compliance Officer" if person else "",
                    extra={"subcategory": subcat, "scrip_code": r.get("scrip_code", "")},
                )
            )

    kws = [k.lower() for k in icp.get("bse_keywords", []) if k]
    if kws:
        rx = re.compile("|".join(re.escape(k) for k in kws), re.I)
        n = 0
        for subcat in GENERIC_SUBCATS:
            for r in _query("Company Update", subcat, min(days, 30), max_pages=6):
                text = f"{r['headline']} {r['more']}"
                m = rx.search(text)
                if not m or not r["company"]:
                    continue
                n += 1
                signals.append(
                    Signal(
                        source="bse", kind="news_trigger", company=r["company"], title=r["headline"][:200],
                        url=r["url"], date=r["date"], snippet=r["more"], relevance=0.5,
                        reason=f"Exchange filing mentions '{m.group(0)}'", extra={"subcategory": subcat, "scrip_code": r.get("scrip_code", "")},
                    )
                )
        if progress:
            progress(f"BSE keyword scan: {n} filings matched {kws}")
    return signals
