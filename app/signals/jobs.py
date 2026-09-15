"""LinkedIn public job postings (India) → 'hiring for the role' signals.

Uses LinkedIn's guest job-search endpoint (no login, no scraping of member data).
A company hiring a "Labour Law Compliance Manager" this month has budget and
attention on exactly the problem the workshop solves.
"""
from __future__ import annotations

import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import httpx
from bs4 import BeautifulSoup

from .. import config
from ..llm import complete_json
from ..store import cache_get, cache_key, cache_put
from .base import Signal

_HEADERS = {"User-Agent": config.USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}
ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
_AGENCY = re.compile(
    r"consultan|recruit|staffing|placement|manpower|hr services|talent|hiring|jobs|careers|"
    r"solutions pvt|workforce|outsourc|search partners|headhunt|executive search",
    re.I,
)
_SYSTEM = """You judge whether a job posting shows that a company is currently staffing the problem a
workshop addresses. For each posting return relevance 0-1 for the workshop and a <=15-word reason.
A posting is relevant when the role would own the topic (e.g. "Manager - HR Compliance" for Labour Codes,
"Company Secretary" for SEBI LODR, "Contracts Manager - EPC" for FIDIC). Unrelated roles = 0.
Postings by staffing agencies, HR consultancies, recruiters or job boards (the company is not the employer) = 0."""


def _fetch(query: str, pages: int = 2) -> list[dict]:
    key = cache_key("lijobs", query, pages, config.LOOKBACK_DAYS)
    hit = cache_get("jobs", key)
    if hit is not None:
        return hit
    seconds = max(config.LOOKBACK_DAYS, 1) * 86400
    out: list[dict] = []
    for p in range(pages):
        url = ENDPOINT + urllib.parse.urlencode(
            {"keywords": query, "location": "India", "f_TPR": f"r{seconds}", "start": p * 10}
        )
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200:
                break
        except Exception:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("li")
        if not cards:
            break
        for li in cards:
            t = li.select_one(".base-search-card__title")
            c = li.select_one(".base-search-card__subtitle")
            loc = li.select_one(".job-search-card__location")
            d = li.select_one("time")
            a = li.select_one("a.base-card__full-link") or li.select_one("a")
            if not (t and c):
                continue
            out.append(
                {
                    "title": t.get_text(strip=True),
                    "company": c.get_text(strip=True),
                    "location": loc.get_text(strip=True) if loc else "",
                    "date": d.get("datetime", "") if d else "",
                    "url": (a.get("href") or "").split("?")[0] if a else "",
                    "query": query,
                }
            )
        if len(cards) < 10:
            break
    cache_put("jobs", key, out)
    return out


def collect(event_title: str, icp: dict, progress=None) -> list[Signal]:
    queries = icp.get("job_queries", [])[:6]
    if not queries:
        return []
    with ThreadPoolExecutor(max_workers=3) as ex:
        batches = list(ex.map(_fetch, queries))
    seen: set[str] = set()
    jobs: list[dict] = []
    for b in batches:
        for j in b:
            k = (j["company"].lower(), j["title"].lower())
            if k in seen or _AGENCY.search(j["company"]):
                continue
            seen.add(k)
            jobs.append(j)
    if progress:
        progress(f"LinkedIn jobs: {len(jobs)} postings for {len(queries)} role queries")
    if not jobs:
        return []

    # LLM relevance pass (cheap, batched) so 'Sales Manager' noise does not become a lead
    scored: dict[int, dict] = {}
    for start in range(0, len(jobs), 30):
        batch = jobs[start : start + 30]
        lines = "\n".join(f"[{i}] {j['title']} @ {j['company']} ({j['location']})" for i, j in enumerate(batch))
        user = f"WORKSHOP: {event_title}\nBUYER TITLES: {', '.join(icp.get('buyer_titles', [])[:8])}\n\nPOSTINGS:\n{lines}\n\nReturn JSON array: [{{\"i\": <index>, \"relevance\": 0.0, \"reason\": \"...\"}}]"
        try:
            rows = complete_json(_SYSTEM, user, model="fast", max_tokens=2500)
        except Exception:
            rows = [{"i": i, "relevance": 0.6, "reason": "matches role query"} for i in range(len(batch))]
        for row in rows if isinstance(rows, list) else []:
            try:
                scored[start + int(row["i"])] = row
            except Exception:
                continue

    signals: list[Signal] = []
    for i, j in enumerate(jobs):
        row = scored.get(i, {"relevance": 0.5, "reason": ""})
        rel = float(row.get("relevance", 0) or 0)
        if rel < 0.4:
            continue
        signals.append(
            Signal(
                source="jobs_linkedin", kind="hiring", company=j["company"], title=j["title"], url=j["url"],
                date=j["date"], location=j["location"], relevance=rel, reason=row.get("reason", ""),
                extra={"query": j["query"]},
            )
        )
    return signals
