"""Google News (India edition) → company-level triggers, extracted by Claude."""
from __future__ import annotations

import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import feedparser
import httpx

from .. import config
from ..llm import complete_json
from ..store import cache_get, cache_key, cache_put
from .base import Signal, iso_date

_HEADERS = {"User-Agent": config.USER_AGENT}

EXTRACT_SYSTEM = """You extract B2B sales triggers from Indian business news for a company that sells
paid compliance/regulatory/contracts training workshops to senior managers.

For each news item decide:
- companies: the specific corporates, banks, NBFCs, PSUs or listed entities that are the SUBJECT of the
  item and would plausibly send staff to the given workshop. Use the common short name
  (e.g. "Tata Motors", "HDFC Bank", "KEC International"). EXCLUDE regulators (SEBI, RBI, ministries, courts),
  law firms, consultancies, media houses, individuals, and generic groups ("banks", "IT firms").
- kind: one of
  regulatory_penalty  (a regulator/exchange/court fined, ordered, censured or investigated the company)
  corporate_event     (IPO/DRHP, merger, acquisition, large order/contract win, restructuring, fund raise, insolvency)
  news_trigger        (labour dispute/strike/layoffs, fraud/breach/cyber incident, arbitration/claims dispute,
                       audit qualification, whistle-blower, other topic-relevant trouble)
  regulatory_change   (industry-wide rule/notification with no specific company)
  job_change          (a named senior appointment relevant to the topic: new CFO, CHRO, CS, GC, Head Contracts ...)
  irrelevant
- relevance: 0.0-1.0 = how strongly this item suggests the company needs THIS workshop in the next 3 months.
- reason: <= 20 words a salesperson can say on a call ("SEBI fined them ₹12L on 3 Sep for RPT disclosure lapse").
- person / person_title: for job_change items, the appointee's name and new title; otherwise "".
Be strict: routine results, share price moves, product launches and generic opinion pieces are irrelevant.
Only India operations count: an event at a foreign parent or in another country is irrelevant unless the item
names the Indian entity (e.g. "Hyundai Motor India")."""


def _fetch_query(query: str) -> list[dict]:
    key = cache_key("gnews", query, config.LOOKBACK_DAYS)
    hit = cache_get("news", key)
    if hit is not None:
        return hit
    q = f"{query} when:{config.LOOKBACK_DAYS}d"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    )
    items: list[dict] = []
    try:
        r = httpx.get(url, headers=_HEADERS, timeout=25, follow_redirects=True)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        for e in feed.entries[: config.MAX_NEWS_ITEMS_PER_QUERY]:
            title = re.sub(r"\s+-\s+[^-]+$", "", e.get("title", "")).strip()  # strip " - Source"
            items.append(
                {
                    "title": title,
                    "url": e.get("link", ""),
                    "date": iso_date(e.get("published_parsed") or e.get("published")),
                    "source": (e.get("source") or {}).get("title", ""),
                    "query": query,
                }
            )
    except Exception as exc:
        items = [{"error": str(exc), "query": query}]
    cache_put("news", key, items)
    return items


def fetch_news(queries: list[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(_fetch_query, queries))
    seen: set[str] = set()
    out: list[dict] = []
    for batch in results:
        for it in batch:
            if "error" in it:
                continue
            k = it["title"].lower()[:80]
            if k in seen or not it["title"]:
                continue
            seen.add(k)
            out.append(it)
    return out


def _extract_batch(batch: list[dict], event_title: str, icp: dict) -> list:
    lines = "\n".join(f"[{i}] ({it['date'] or 'n/d'}; {it['source']}) {it['title']}" for i, it in enumerate(batch))
    user = f"""WORKSHOP: {event_title}
TARGET BUYERS: {', '.join(icp.get('buyer_titles', [])[:8])}
TARGET INDUSTRIES: {', '.join(icp.get('industries', [])[:8])}

NEWS ITEMS:
{lines}

Return a JSON array with one object per item: {{"i": <index>, "companies": [..], "kind": "...", "relevance": 0.0, "reason": "...", "person": "", "person_title": ""}}"""
    rows = complete_json(EXTRACT_SYSTEM, user, model="default", max_tokens=3500)
    return rows if isinstance(rows, list) else []


def extract_signals(items: list[dict], event_title: str, icp: dict, progress=None) -> list[Signal]:
    signals: list[Signal] = []
    batch_size = 20
    batches = [items[s : s + batch_size] for s in range(0, len(items), batch_size)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_extract_batch, b, event_title, icp) for b in batches]
    for bi, (batch, fut) in enumerate(zip(batches, futures)):
        try:
            rows = fut.result()
        except Exception as exc:
            if progress:
                progress(f"news extraction batch {bi + 1} failed: {exc}")
            continue
        for row in rows:
            try:
                it = batch[int(row["i"])]
            except Exception:
                continue
            kind = row.get("kind", "irrelevant")
            rel = float(row.get("relevance", 0) or 0)
            if kind in ("irrelevant",) or rel < 0.35:
                continue
            for comp in row.get("companies", []) or []:
                if not comp or len(comp) < 3:
                    continue
                signals.append(
                    Signal(
                        source="news",
                        kind=kind if kind != "regulatory_change" else "regulatory_change",
                        company=comp,
                        title=it["title"],
                        url=it["url"],
                        date=it["date"],
                        snippet=it["source"],
                        relevance=rel,
                        reason=row.get("reason", ""),
                        person_name=(row.get("person") or "").strip(),
                        person_title=(row.get("person_title") or "").strip(),
                        extra={"query": it["query"]},
                    )
                )
    if progress:
        progress(f"news: extracted {len(signals)} company signals from {len(items)} items")
    return signals
