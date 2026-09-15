"""LinkedIn content engagement → PERSON-level intent (optional; needs APIFY_TOKEN).

People who reacted to / commented on a recent post about "Labour Codes" or "SEBI LODR"
are, by definition, in-market for that topic. This is the honest India-specific equivalent
of the "someone searched your topic" story ZoomInfo sells — and it names the person.

Uses harvestapi's no-cookie actors on Apify (no member login required):
  harvestapi/linkedin-post-search     → recent posts for the topic keywords
  harvestapi/linkedin-post-reactions  → who reacted (name, headline, profile url)
  harvestapi/linkedin-post-comments   → who commented
"""
from __future__ import annotations

import re

import httpx

from .. import config
from ..llm import complete_json
from ..store import cache_get, cache_key, cache_put
from .base import Signal, iso_date

APIFY = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
ACTOR_SEARCH = config.APIFY_POST_SEARCH_ACTOR or "harvestapi~linkedin-post-search"
ACTOR_REACTIONS = config.APIFY_POST_ENGAGERS_ACTOR or "harvestapi~linkedin-post-reactions"
ACTOR_COMMENTS = "harvestapi~linkedin-post-comments"

_SYSTEM = """You qualify LinkedIn members who engaged with content about a workshop topic.
For each person, using their headline, return: title (their job title), company (short common name, "" if
unknown), relevance 0-1 = likelihood they are a decision maker / delegate for THIS workshop at an Indian
corporate (students, job seekers, consultants selling services, recruiters and people outside India = 0),
reason <= 12 words."""


def _run(actor: str, payload: dict, timeout: int = 300) -> list[dict]:
    key = cache_key("apify", actor, payload)
    hit = cache_get("linkedin", key)
    if hit is not None:
        return hit
    url = APIFY.format(actor=actor) + f"?token={config.APIFY_TOKEN}&timeout={timeout}"
    r = httpx.post(url, json=payload, timeout=timeout + 30)
    r.raise_for_status()
    data = r.json()
    items = data if isinstance(data, list) else data.get("items", [])
    cache_put("linkedin", key, items)
    return items


def _first(d: dict, *keys, default=""):
    for k in keys:
        cur = d
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur:
            return cur
    return default


def _person_from(item: dict) -> dict:
    actor = item.get("actor") or item.get("author") or item.get("profile") or item
    name = _first(actor, "name", "fullName", "title") or " ".join(
        x for x in (_first(actor, "firstName"), _first(actor, "lastName")) if x
    )
    return {
        "name": name.strip(),
        "headline": _first(actor, "headline", "description", "position", "occupation"),
        "url": _first(actor, "linkedinUrl", "url", "profileUrl", "publicIdentifier"),
        "company": _first(actor, "currentPosition.companyName", "company.name", "companyName", "company"),
        "location": _first(actor, "location", "location.linkedinText"),
    }


def collect(event_title: str, icp: dict, progress=None) -> list[Signal]:
    if not config.APIFY_TOKEN:
        if progress:
            progress("LinkedIn engagement lane skipped (APIFY_TOKEN not set)")
        return []
    keywords = icp.get("linkedin_keywords", [])[:4]
    if not keywords:
        return []

    try:
        posts = _run(ACTOR_SEARCH, {"searchQueries": keywords, "maxPosts": 6, "postedLimit": "month", "sortBy": "relevance"})
    except Exception as exc:
        if progress:
            progress(f"LinkedIn post search failed: {exc}")
        return []
    post_urls = []
    post_meta: dict[str, dict] = {}
    for p in posts:
        u = _first(p, "linkedinUrl", "url", "postUrl")
        if not u or u in post_meta:
            continue
        post_meta[u] = {
            "text": (_first(p, "content", "text", "commentary") or "")[:200],
            "author": _first(p, "author.name", "authorName", "author.fullName"),
            "date": iso_date(_first(p, "postedAt.date", "postedAt", "date", "publishedAt")),
        }
        post_urls.append(u)
    if progress:
        progress(f"LinkedIn: {len(post_urls)} recent posts for {keywords}")
    if not post_urls:
        return []
    post_urls = post_urls[:12]

    engagers: list[dict] = []
    for actor, kind_label in ((ACTOR_REACTIONS, "reacted to"), (ACTOR_COMMENTS, "commented on")):
        try:
            payload = {"posts": post_urls, "maxItems": 40, "profileScraperMode": "Short"}
            items = _run(actor, payload)
        except Exception as exc:
            if progress:
                progress(f"LinkedIn {kind_label} scrape failed: {exc}")
            continue
        for it in items:
            person = _person_from(it)
            if not person["name"] or not person["headline"]:
                continue
            post_url = _first(it, "postUrl", "post.linkedinUrl", "post.url", "inputUrl") or post_urls[0]
            engagers.append({**person, "post_url": post_url, "how": kind_label,
                             "comment": (_first(it, "commentary", "text", "comment") or "")[:200]})
    # de-dupe by profile url / name
    seen: set[str] = set()
    uniq = []
    for e in engagers:
        k = e["url"] or e["name"].lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    if progress:
        progress(f"LinkedIn: {len(uniq)} unique people engaged with the topic")
    if not uniq:
        return []

    signals: list[Signal] = []
    for start in range(0, len(uniq), 30):
        batch = uniq[start : start + 30]
        lines = "\n".join(f"[{i}] {e['name']} — {e['headline']} ({e['location']})" for i, e in enumerate(batch))
        user = f"WORKSHOP: {event_title}\nBUYER TITLES: {', '.join(icp.get('buyer_titles', [])[:10])}\n\nPEOPLE:\n{lines}\n\nReturn JSON array: [{{\"i\": <index>, \"title\": \"...\", \"company\": \"...\", \"relevance\": 0.0, \"reason\": \"...\"}}]"
        try:
            rows = complete_json(_SYSTEM, user, model="fast", max_tokens=3000)
        except Exception:
            rows = []
        for row in rows if isinstance(rows, list) else []:
            try:
                e = batch[int(row["i"])]
            except Exception:
                continue
            rel = float(row.get("relevance", 0) or 0)
            company = (row.get("company") or e["company"] or "").strip()
            if rel < 0.45 or not company:
                continue
            meta = post_meta.get(e["post_url"], {})
            signals.append(
                Signal(
                    source="linkedin_post", kind="engagement", company=company,
                    title=f"{e['how']} a post about {icp.get('topic', event_title)}",
                    url=e["post_url"], date=meta.get("date", ""), snippet=meta.get("text", ""),
                    relevance=rel, reason=row.get("reason", ""), location=e["location"],
                    person_name=e["name"], person_title=row.get("title") or e["headline"], person_url=e["url"],
                    extra={"post_author": meta.get("author", ""), "comment": e["comment"]},
                )
            )
    return signals
