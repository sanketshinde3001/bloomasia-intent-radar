"""RBI and SEBI official feeds → penalties / orders naming a company.

These are the "must act" signals: a regulator has just fined, censured or opened an
enquiry against the company. Both regulators publish RSS; Google News (news.py) backfills
the older window.
"""
from __future__ import annotations

import re

import feedparser
import httpx

from .. import config
from ..llm import complete_json
from ..store import cache_get, cache_key, cache_put
from .base import Signal, iso_date

_HEADERS = {"User-Agent": config.USER_AGENT}
FEEDS = {
    "rbi": "https://www.rbi.org.in/pressreleases_rss.xml",
    "sebi": "https://www.sebi.gov.in/sebirss.xml",
}
# titles worth looking at (case-insensitive)
_KEEP = re.compile(
    r"penalty|adjudication order|order in the matter|enquiry order|settlement order|show cause|"
    r"final order|interim order|confirmatory order|cancell|restriction|direction|debar|fine|"
    r"non-compliance|violation|ban ",
    re.I,
)
_DROP = re.compile(r"appeal no\.|release order|recovery|auction|tender|vacancy|recruit|felicitation|"
                   r"money market|auction result|treasury bill|state government|reference rate|weekly statistical",
                   re.I)

_SYSTEM = """You classify official RBI / SEBI publications as sales triggers for a company that sells paid
compliance workshops to senior managers at Indian corporates, banks and NBFCs.
For each item: company = the regulated entity named (short common name, e.g. "Bandhan Bank",
"Modex International Securities"); "" if the item is generic or names only an individual/investment adviser
who would never buy corporate training. kind = regulatory_penalty | regulatory_change | irrelevant.
relevance 0-1 for THIS workshop; reason <= 20 words a salesperson could say on a call.
Small stock brokers, individual investment advisers, cooperative banks with <₹500cr and research analysts are
usually irrelevant for corporate-training sales; scheduled banks, NBFCs, listed companies, ARCs and large
intermediaries are relevant when the topic matches."""


def fetch_feed(name: str) -> list[dict]:
    key = cache_key("regfeed", name)
    hit = cache_get("regulators", key)
    if hit is not None:
        return hit
    items: list[dict] = []
    try:
        r = httpx.get(FEEDS[name], headers=_HEADERS, timeout=25, follow_redirects=True)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        for e in feed.entries:
            title = (e.get("title") or "").strip()
            if not title or _DROP.search(title) or not _KEEP.search(title):
                continue
            desc = re.sub(r"<[^>]+>", " ", e.get("summary", "") or e.get("description", ""))
            desc = re.sub(r"\s+", " ", desc).strip()[:600]
            items.append(
                {
                    "regulator": name.upper(),
                    "title": title,
                    "url": e.get("link", ""),
                    "date": iso_date(e.get("published_parsed") or e.get("published")),
                    "snippet": desc,
                }
            )
    except Exception as exc:
        items = [{"error": str(exc)}]
    cache_put("regulators", key, items)
    return [i for i in items if "error" not in i]


def collect(event_title: str, icp: dict, progress=None) -> list[Signal]:
    regs = [r.lower() for r in icp.get("regulators", [])]
    wanted = [n for n in FEEDS if n in regs]
    if not wanted:
        return []
    items: list[dict] = []
    for n in wanted:
        got = fetch_feed(n)
        if progress:
            progress(f"{n.upper()} feed: {len(got)} candidate orders/releases")
        items.extend(got)
    if not items:
        return []

    signals: list[Signal] = []
    for start in range(0, len(items), 15):
        batch = items[start : start + 15]
        lines = "\n".join(
            f"[{i}] {it['regulator']} {it['date']}: {it['title']} — {it['snippet'][:220]}" for i, it in enumerate(batch)
        )
        user = f"""WORKSHOP: {event_title}
TARGET BUYERS: {', '.join(icp.get('buyer_titles', [])[:8])}

ITEMS:
{lines}

Return a JSON array: [{{"i": <index>, "company": "...", "kind": "...", "relevance": 0.0, "reason": "..."}}]"""
        try:
            rows = complete_json(_SYSTEM, user, max_tokens=2500)
        except Exception as exc:
            if progress:
                progress(f"regulator classification failed: {exc}")
            continue
        for row in rows if isinstance(rows, list) else []:
            try:
                it = batch[int(row["i"])]
            except Exception:
                continue
            comp = (row.get("company") or "").strip()
            kind = row.get("kind", "irrelevant")
            rel = float(row.get("relevance", 0) or 0)
            if not comp or kind == "irrelevant" or rel < 0.35:
                continue
            signals.append(
                Signal(
                    source=it["regulator"].lower(),
                    kind=kind,
                    company=comp,
                    title=it["title"],
                    url=it["url"],
                    date=it["date"],
                    snippet=it["snippet"][:300],
                    relevance=rel,
                    reason=row.get("reason", ""),
                )
            )
    return signals
