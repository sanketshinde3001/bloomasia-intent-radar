"""Free LinkedIn engagement lane (no Apify, no login) — best effort.

1. Discover recent public LinkedIn posts about the topic through DuckDuckGo
   (site:linkedin.com/posts "<keyword>").
2. Fetch each post as a guest: LinkedIn serves public posts with the author,
   the post text, a relative age and the first few comments (name + profile URL + text).
3. Qualify authors/commenters with the LLM (job seekers, consultants, students → out).

Limitations vs the Apify lane: no headline/company for members (LinkedIn returns 999 for
guest profile fetches), reactions ("likes") are not exposed, and DuckDuckGo rate-limits
(HTTP 202) after a handful of queries. Good enough to show named, dated, person-level
engagement for a demo; use APIFY_TOKEN (free plan has $5/month credit) for production.
"""
from __future__ import annotations

import re
import time
import urllib.parse
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup

from .. import config
from ..llm import complete_json
from ..store import cache_get, cache_key, cache_put
from .base import Signal

_UA = {"User-Agent": config.USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}
_DDG = "https://html.duckduckgo.com/html/"
_AGE = re.compile(r"(\d+)\s*(h|d|w|mo|y)\b", re.I)
_UNITS = {"h": 0, "d": 1, "w": 7, "mo": 30, "y": 365}

_SYSTEM = """You qualify people who wrote or commented on LinkedIn posts about a workshop topic in India.
You only get their name, what they wrote, and sometimes a profile slug. Return for each person:
- title: best guess of job role from the text ("" if no clue)
- company: short company name if it is stated or clearly implied ("" otherwise — do NOT guess)
- relevance 0-1: likelihood they are a practitioner (HR/CS/legal/contracts/compliance/finance manager at a corporate)
  who could attend or sponsor THIS workshop. Job seekers, students, consultants/law firms selling services,
  trainers, recruiters, and generic "great post" comments = 0.2 or lower.
- reason: <= 12 words."""


def _ddg(query: str) -> list[dict] | None:
    """Search results, or None when DuckDuckGo is throttling (HTTP 202) — callers should stop."""
    key = cache_key("ddg", query, date.today().isoformat())
    hit = cache_get("linkedin_free", key)
    if hit is not None:
        return hit
    out: list[dict] = []
    status = 0
    for attempt in range(2):
        try:
            r = httpx.post(_DDG, data={"q": query}, headers=_UA, timeout=25, follow_redirects=True)
            status = r.status_code
        except Exception:
            break
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a.result__a"):
                href = a.get("href", "")
                m = re.search(r"uddg=([^&]+)", href)
                url = urllib.parse.unquote(m.group(1)) if m else href
                if "linkedin.com/posts/" in url:
                    out.append({"url": url.split("?")[0], "title": a.get_text(" ", strip=True)})
            break
        time.sleep(6)  # 202 = DuckDuckGo rate limit; one retry
    if status != 200:
        return None  # throttled: do not cache, tell the caller to stop
    cache_put("linkedin_free", key, out)
    return out


def _age_days(text: str) -> int | None:
    m = _AGE.search(text or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * _UNITS.get(unit, 30) if unit != "h" else 0


def _fetch_post(url: str) -> dict | None:
    key = cache_key("lipost", url)
    hit = cache_get("linkedin_free", key)
    if hit is not None:
        return hit or None
    try:
        r = httpx.get(url, headers=_UA, timeout=25, follow_redirects=True)
        if r.status_code != 200 or "linkedin.com" not in str(r.url):
            cache_put("linkedin_free", key, {})
            return None
    except Exception:
        return None
    s = BeautifulSoup(r.text, "html.parser")
    actor = s.select_one('[data-tracking-control-name="public_post_feed-actor-name"]')
    body = s.select_one(".attributed-text-segment-list__content") or s.select_one('[data-test-id="main-feed-activity-card__commentary"]')
    tm = s.select_one("time")
    post = {
        "url": url,
        "author": actor.get_text(" ", strip=True) if actor else "",
        "author_url": (actor.get("href", "") if actor else "").split("?")[0],
        "text": body.get_text(" ", strip=True)[:400] if body else "",
        "age_days": _age_days(tm.get_text(strip=True) if tm else ""),
        "comments": [],
    }
    m = re.search(r'data-num-reactions="(\d+)"', r.text) or re.search(r"(\d[\d,]*)\s+Reactions?", r.text)
    post["reactions"] = int(m.group(1).replace(",", "")) if m else None
    for c in s.select("section.comment"):
        au = c.select_one(".comment__author")
        txt = c.select_one(".comment__text")
        age = c.select_one(".comment__duration-since")
        if not au:
            continue
        post["comments"].append(
            {
                "name": au.get_text(" ", strip=True),
                "url": (au.get("href", "") or "").split("?")[0],
                "text": txt.get_text(" ", strip=True)[:300] if txt else "",
                "age_days": _age_days(age.get_text(strip=True) if age else ""),
            }
        )
    cache_put("linkedin_free", key, post)
    return post


def collect(event_title: str, icp: dict, progress=None) -> list[Signal]:
    if not config.LINKEDIN_FREE_LANE or config.APIFY_TOKEN:
        return []  # Apify lane takes over when a token exists
    keywords = [k for k in icp.get("linkedin_keywords", []) if k][:3]
    if not keywords:
        return []

    posts: dict[str, dict] = {}
    throttled = False
    for kw in keywords:
        hits = _ddg(f'site:linkedin.com/posts "{kw}"')
        if hits is None:
            throttled = True
            break
        for hit in hits[:8]:
            posts.setdefault(hit["url"], hit)
        time.sleep(1.5)
    if progress:
        progress(f"LinkedIn (free lane): {len(posts)} public posts found via DuckDuckGo for {keywords}"
                 + (" — DuckDuckGo throttled (HTTP 202); retry later or set APIFY_TOKEN" if throttled else ""))
    if not posts:
        return []

    people: list[dict] = []
    fetched = 0
    for url in list(posts)[:14]:
        p = _fetch_post(url)
        time.sleep(1.2)
        if not p:
            continue
        fetched += 1
        max_age = config.LOOKBACK_DAYS * 2
        # post age is sometimes missing from the guest page; fall back to the newest comment's age
        post_age = p["age_days"]
        if post_age is None:
            ages = [c["age_days"] for c in p["comments"] if c["age_days"] is not None]
            post_age = min(ages) if ages else None
        if post_age is None or post_age > max_age:
            continue  # stale or undatable post
        when = (date.today() - timedelta(days=post_age)).isoformat()
        if p["author"]:
            people.append({"name": p["author"], "url": p["author_url"], "how": "posted", "text": p["text"],
                           "post_url": url, "date": when, "post_text": p["text"], "reactions": p["reactions"]})
        for c in p["comments"]:
            if not c["name"]:
                continue
            c_age = c["age_days"] if c["age_days"] is not None else post_age
            if c_age > max_age:
                continue
            cw = (date.today() - timedelta(days=c_age)).isoformat()
            people.append({"name": c["name"], "url": c["url"], "how": "commented on", "text": c["text"],
                           "post_url": url, "date": cw, "post_text": p["text"], "reactions": p["reactions"]})
    seen: set[str] = set()
    uniq = []
    for e in people:
        k = e["url"] or e["name"].lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    if progress:
        progress(f"LinkedIn (free lane): {fetched} posts read, {len(uniq)} authors/commenters found")
    if not uniq:
        return []

    signals: list[Signal] = []
    for start in range(0, len(uniq), 25):
        batch = uniq[start : start + 25]
        lines = "\n".join(
            f"[{i}] {e['name']} ({e['url'].split('/in/')[-1] if '/in/' in e['url'] else 'company page'}) {e['how']}: "
            f"\"{(e['text'] or '')[:180]}\"" for i, e in enumerate(batch)
        )
        user = f"WORKSHOP: {event_title}\nBUYER TITLES: {', '.join(icp.get('buyer_titles', [])[:10])}\n\nPEOPLE:\n{lines}\n\nReturn JSON array: [{{\"i\": <index>, \"title\": \"...\", \"company\": \"...\", \"relevance\": 0.0, \"reason\": \"...\"}}]"
        try:
            rows = complete_json(_SYSTEM, user, model="fast", max_tokens=3000)
        except Exception as exc:
            if progress:
                progress(f"LinkedIn (free lane) qualification failed: {exc}")
            rows = []
        for row in rows if isinstance(rows, list) else []:
            try:
                e = batch[int(row["i"])]
            except Exception:
                continue
            rel = float(row.get("relevance", 0) or 0)
            if rel < 0.45:
                continue
            signals.append(
                Signal(
                    source="linkedin_public", kind="engagement", company=(row.get("company") or "").strip(),
                    title=f"{e['how']} a LinkedIn post about {icp.get('topic', event_title)}",
                    url=e["post_url"], date=e["date"], snippet=(e["post_text"] or "")[:200],
                    relevance=rel, reason=row.get("reason", ""),
                    person_name=e["name"], person_title=row.get("title") or "", person_url=e["url"],
                    extra={"comment": e["text"], "reactions": e["reactions"]},
                )
            )
    return signals
